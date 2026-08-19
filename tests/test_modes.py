import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from acp_server.model_catalog import AIDER_ACP_MODELS_ENV
from acp_server.server import AiderAgent
from acp_server.session import AiderSession
from acp_server.session_modes import aider_edit_format, infer_mode_id
from aider.commands import SwitchCoder
from test_commands import FakeCoder
from test_models import FakeConn, fake_validate_environment


def _mode_updates(conn):
    return [
        u
        for u in conn.updates
        if getattr(u, "session_update", None) == "current_mode_update"
    ]


class SessionModesCatalogTests(unittest.TestCase):
    def test_aider_edit_format(self):
        with patch("acp_server.session_modes.Model") as model_cls:
            model_cls.return_value.edit_format = "diff"
            self.assertEqual(aider_edit_format("ask", "gpt-4o"), "ask")
            self.assertEqual(aider_edit_format("architect", "gpt-4o"), "architect")
            self.assertEqual(aider_edit_format("code", "gpt-4o"), "diff")
        with self.assertRaises(ValueError):
            aider_edit_format("review", "gpt-4o")

    def test_infer_mode_id(self):
        class Coder:
            def __init__(self, edit_format):
                self.edit_format = edit_format

        self.assertEqual(infer_mode_id(Coder("ask")), "ask")
        self.assertEqual(infer_mode_id(Coder("architect")), "architect")
        self.assertEqual(infer_mode_id(Coder("code")), "code")
        self.assertEqual(infer_mode_id(Coder("diff")), "code")
        self.assertEqual(infer_mode_id(Coder("udiff")), "code")
        self.assertIsNone(infer_mode_id(Coder("help")))
        self.assertIsNone(infer_mode_id(Coder("context")))
        self.assertIsNone(infer_mode_id(object()))


class NewSessionModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_session_returns_modes(self):
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

        self.assertEqual(response.modes.current_mode_id, "code")
        self.assertEqual(
            [m.id for m in response.modes.available_modes],
            ["ask", "code", "architect"],
        )
        session = agent.sessions[response.session_id]
        self.assertEqual(session.current_mode_id, "code")


class SetSessionModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_session_mode_without_coder(self):
        agent = AiderAgent()
        conn = FakeConn()
        loop = asyncio.get_running_loop()
        session = AiderSession(
            session_id="s1",
            connection=conn,
            loop=loop,
            cwd=tempfile.gettempdir(),
            available_model_ids=["gpt-4o"],
            current_model_id="gpt-4o",
        )
        agent.sessions["s1"] = session

        with patch("acp_server.session.switch_coder") as switch_mock:
            await agent.set_session_mode(mode_id="ask", session_id="s1")
        await asyncio.sleep(0.05)

        self.assertEqual(session.current_mode_id, "ask")
        switch_mock.assert_not_called()
        mode_updates = _mode_updates(conn)
        self.assertEqual(len(mode_updates), 1)
        self.assertEqual(mode_updates[0].current_mode_id, "ask")
        chunks = [
            u
            for u in conn.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        ]
        self.assertTrue(any("Mode: Ask" in u.content.text for u in chunks))

    async def test_set_session_mode_with_coder(self):
        agent = AiderAgent()
        conn = FakeConn()
        loop = asyncio.get_running_loop()
        session = AiderSession(
            session_id="s1",
            connection=conn,
            loop=loop,
            cwd=tempfile.gettempdir(),
            available_model_ids=["gpt-4o"],
            current_model_id="gpt-4o",
        )
        session.coder = FakeCoder()
        agent.sessions["s1"] = session
        new_coder = FakeCoder()
        new_coder.edit_format = "ask"

        with patch(
            "acp_server.session.switch_coder", return_value=new_coder
        ) as switch_mock:
            await agent.set_session_mode(mode_id="ask", session_id="s1")

        switch_mock.assert_called_once()
        self.assertEqual(switch_mock.call_args.kwargs["edit_format"], "ask")
        self.assertIs(session.coder, new_coder)
        self.assertEqual(_mode_updates(conn)[0].current_mode_id, "ask")

    async def test_set_session_mode_rejects_unknown_mode(self):
        agent = AiderAgent()
        agent.sessions["s1"] = type(
            "S",
            (),
            {
                "prompt_running": False,
                "set_mode": AsyncMock(),
            },
        )()

        with self.assertRaises(ValueError) as ctx:
            await agent.set_session_mode(mode_id="review", session_id="s1")
        self.assertIn("Unknown mode", str(ctx.exception))

    async def test_set_session_mode_rejects_while_prompt_running(self):
        agent = AiderAgent()
        agent.sessions["s1"] = type(
            "S",
            (),
            {
                "prompt_running": True,
            },
        )()

        with self.assertRaises(ValueError) as ctx:
            await agent.set_session_mode(mode_id="ask", session_id="s1")
        self.assertIn("prompt is running", str(ctx.exception))


class RunPromptModeTests(unittest.IsolatedAsyncioTestCase):
    def _validate_ok(self):
        def validate(self):
            self.missing_keys = []
            self.keys_in_environment = True
            return {"keys_in_environment": True, "missing_keys": []}

        return patch("aider_bridge.factory.Model.validate_environment", validate)

    async def _make_session(self, conn=None):
        loop = asyncio.get_running_loop()
        conn = conn or FakeConn()
        session = AiderSession(
            session_id="s1",
            connection=conn,
            loop=loop,
            cwd=tempfile.gettempdir(),
            available_model_ids=["gpt-4o"],
            current_model_id="gpt-4o",
        )
        session.coder = FakeCoder()
        return session, conn

    async def test_ask_one_shot_keeps_code_mode(self):
        session, conn = await self._make_session()
        new_coder = FakeCoder()
        new_coder.edit_format = "code"
        session.coder = FakeCoder(
            run_side_effect=SwitchCoder(
                edit_format="code",
                summarize_from_coder=False,
                from_coder=FakeCoder(),
                show_announcements=False,
            )
        )

        with self._validate_ok():
            with patch(
                "acp_server.session.switch_coder", return_value=new_coder
            ):
                stop_reason = await session.run_prompt("/ask what is this?")

        self.assertEqual(stop_reason, "end_turn")
        self.assertEqual(session.current_mode_id, "code")
        self.assertEqual(_mode_updates(conn), [])
        chunks = [
            u
            for u in conn.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        ]
        self.assertFalse(any("Mode:" in getattr(u.content, "text", "") for u in chunks))

    async def test_ask_persistent_sets_ask_mode(self):
        session, conn = await self._make_session()
        new_coder = FakeCoder()
        new_coder.edit_format = "ask"
        session.coder = FakeCoder(
            run_side_effect=SwitchCoder(edit_format="ask")
        )

        with self._validate_ok():
            with patch(
                "acp_server.session.switch_coder", return_value=new_coder
            ):
                stop_reason = await session.run_prompt("/ask")

        self.assertEqual(stop_reason, "end_turn")
        self.assertEqual(session.current_mode_id, "ask")
        self.assertEqual(len(_mode_updates(conn)), 1)
        self.assertEqual(_mode_updates(conn)[0].current_mode_id, "ask")


if __name__ == "__main__":
    unittest.main()
