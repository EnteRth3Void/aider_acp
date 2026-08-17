import asyncio
import os
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from acp.schema import TextContentBlock

from acp_server.model_catalog import (
    AIDER_ACP_MODELS_ENV,
    build_session_model_state,
    catalog_error_message,
    filter_available_models,
    parse_catalog_env,
)
from acp_server.server import AiderAgent
from aider_bridge.io_bridge import ACPIO


class FakeConn:
    def __init__(self):
        self.updates = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)


def asyncio_new_running_loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    loop._test_thread = thread
    return loop


def stop_loop(loop):
    loop.call_soon_threadsafe(loop.stop)
    thread = getattr(loop, "_test_thread", None)
    if thread is not None:
        thread.join(timeout=2)
    loop.close()


def fake_validate_environment(model_id: str, keys: set[str]):
    def _validate(self):
        openai_models = {"gpt-4o"}
        anthropic_models = {"anthropic/claude-sonnet-4-20250514"}
        if model_id in openai_models:
            missing = [] if "OPENAI_API_KEY" in keys else ["OPENAI_API_KEY"]
        elif model_id in anthropic_models:
            missing = [] if "ANTHROPIC_API_KEY" in keys else ["ANTHROPIC_API_KEY"]
        else:
            missing = ["UNKNOWN_API_KEY"]
        keys_ok = not missing
        self.missing_keys = missing
        self.keys_in_environment = keys_ok
        return {"keys_in_environment": keys_ok, "missing_keys": missing}

    return _validate


class ModelCatalogTests(unittest.TestCase):
    def test_parse_catalog_from_env_only(self):
        with patch.dict(
            os.environ,
            {AIDER_ACP_MODELS_ENV: "gpt-4o, anthropic/claude-sonnet-4-20250514"},
            clear=False,
        ):
            self.assertEqual(
                parse_catalog_env(),
                ["gpt-4o", "anthropic/claude-sonnet-4-20250514"],
            )

    def test_filter_by_api_keys(self):
        models = ["gpt-4o", "anthropic/claude-sonnet-4-20250514"]

        def validate(self):
            return fake_validate_environment(self.name, {"OPENAI_API_KEY"})(self)

        with patch("acp_server.model_catalog.Model.validate_environment", validate):
            filtered = filter_available_models(models)
        self.assertEqual(filtered, ["gpt-4o"])

    def test_catalog_error_when_env_missing(self):
        msg = catalog_error_message([])
        self.assertIn(AIDER_ACP_MODELS_ENV, msg)
        self.assertIn("OPENAI_API_KEY", msg)

    def test_build_session_model_state(self):
        state = build_session_model_state(["gpt-4o", "gpt-4.1"], "gpt-4o")
        self.assertEqual(state.current_model_id, "gpt-4o")
        self.assertEqual(
            [m.model_id for m in state.available_models],
            ["gpt-4o", "gpt-4.1"],
        )
        self.assertEqual([m.name for m in state.available_models], ["gpt-4o", "gpt-4.1"])


class NewSessionModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_session_fails_without_models_env(self):
        agent = AiderAgent()
        env = {k: v for k, v in os.environ.items() if k != AIDER_ACP_MODELS_ENV}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError) as ctx:
                await agent.new_session(cwd=tempfile.gettempdir())
        self.assertIn(AIDER_ACP_MODELS_ENV, str(ctx.exception))

    async def test_new_session_fails_when_no_keys_match(self):
        agent = AiderAgent()
        env = {
            AIDER_ACP_MODELS_ENV: "gpt-4o,anthropic/claude-sonnet-4-20250514",
        }
        with patch.dict(os.environ, env, clear=True):
            def validate(self):
                return fake_validate_environment(self.name, set())(self)

            with patch("acp_server.model_catalog.Model.validate_environment", validate):
                with self.assertRaises(ValueError) as ctx:
                    await agent.new_session(cwd=tempfile.gettempdir())
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))

    async def test_new_session_returns_models(self):
        agent = AiderAgent()
        agent.client = FakeConn()
        env = {
            AIDER_ACP_MODELS_ENV: "gpt-4o,anthropic/claude-sonnet-4-20250514",
            "OPENAI_API_KEY": "sk-test",
        }
        with patch.dict(os.environ, env, clear=False):
            def validate(self):
                return fake_validate_environment(self.name, {"OPENAI_API_KEY"})(self)

            with patch("acp_server.model_catalog.Model.validate_environment", validate):
                response = await agent.new_session(cwd=tempfile.gettempdir())

        self.assertEqual(response.models.current_model_id, "gpt-4o")
        self.assertEqual(
            [m.model_id for m in response.models.available_models],
            ["gpt-4o"],
        )
        session = agent.sessions[response.session_id]
        self.assertEqual(session.current_model_id, "gpt-4o")
        self.assertEqual(session.available_model_ids, ["gpt-4o"])


class SetSessionModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_session_model_updates_current_model_id(self):
        agent = AiderAgent()
        session_id = "s1"
        agent.sessions[session_id] = type(
            "S",
            (),
            {
                "prompt_running": False,
                "available_model_ids": ["gpt-4o", "gpt-4.1"],
                "current_model_id": "gpt-4o",
                "coder": None,
                "set_model": AsyncMock(),
            },
        )()

        await agent.set_session_model(model_id="gpt-4.1", session_id=session_id)
        agent.sessions[session_id].set_model.assert_awaited_once_with("gpt-4.1")

    async def test_set_session_model_rejects_while_prompt_running(self):
        agent = AiderAgent()
        session_id = "s1"
        agent.sessions[session_id] = type(
            "S",
            (),
            {
                "prompt_running": True,
                "available_model_ids": ["gpt-4o"],
                "current_model_id": "gpt-4o",
            },
        )()

        with self.assertRaises(ValueError) as ctx:
            await agent.set_session_model(model_id="gpt-4o", session_id=session_id)
        self.assertIn("prompt is running", str(ctx.exception))


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_keys_blocks_prompt_without_run(self):
        from acp_server.session import AiderSession

        loop = asyncio.get_running_loop()
        conn = FakeConn()
        session = AiderSession(
            session_id="s1",
            connection=conn,
            loop=loop,
            cwd=tempfile.gettempdir(),
            available_model_ids=["gpt-4o"],
            current_model_id="gpt-4o",
        )

        run_called = False

        class FakeCoder:
            root = tempfile.gettempdir()

            def run(self, with_message=None):
                nonlocal run_called
                run_called = True

        session.coder = FakeCoder()

        def validate(self):
            self.missing_keys = ["OPENAI_API_KEY"]
            self.keys_in_environment = False
            return {"keys_in_environment": False, "missing_keys": ["OPENAI_API_KEY"]}

        with patch("aider_bridge.factory.Model.validate_environment", validate):
            stop_reason = await session.run_prompt("hello")
            await asyncio.sleep(0.05)

        self.assertEqual(stop_reason, "end_turn")
        self.assertFalse(run_called)
        kinds = [
            getattr(u, "session_update", None) or getattr(u, "sessionUpdate", None)
            for u in conn.updates
        ]
        self.assertIn("agent_message_chunk", kinds)


class ToolErrorTests(unittest.TestCase):
    def test_tool_error_emits_agent_message_chunk(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        loop = asyncio_new_running_loop()
        self.addCleanup(lambda: stop_loop(loop))
        conn = FakeConn()
        io = ACPIO(
            session_id="s1",
            connection=conn,
            loop=loop,
            root=tmp.name,
        )
        io.tool_error("authentication failed")
        update = conn.updates[-1]
        kind = getattr(update, "session_update", None) or getattr(update, "sessionUpdate", None)
        self.assertEqual(kind, "agent_message_chunk")
        self.assertIn("authentication failed", update.content.text)


if __name__ == "__main__":
    unittest.main()
