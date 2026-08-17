import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from acp_server.available_commands import curated_available_commands
from acp_server.server import AiderAgent
from acp_server.session import (
    AiderSession,
    REVIEW_MODE_DENIED_COMMANDS,
    _command_name_from_text,
    _is_command,
)
from aider.commands import SwitchCoder
from test_models import FakeConn, fake_validate_environment


class FakeModel:
    def __init__(self, name: str):
        self.name = name


class FakeCoder:
    root = tempfile.gettempdir()
    repo = None

    def __init__(self, run_side_effect=None):
        self.main_model = FakeModel("gpt-4o")
        self.weak_model = FakeModel("gpt-4o-mini")
        self.editor_model = FakeModel("gpt-4o")
        self._run_side_effect = run_side_effect

    def run(self, with_message=None):
        if self._run_side_effect is not None:
            raise self._run_side_effect


class CommandDetectionTests(unittest.TestCase):
    def test_is_command_recognizes_slash_and_bang(self):
        self.assertTrue(_is_command("/model gpt-4.1"))
        self.assertTrue(_is_command("  !ls"))
        self.assertFalse(_is_command("hello"))

    def test_command_name_maps_bang_to_run(self):
        self.assertEqual(_command_name_from_text("!ls"), "run")
        self.assertEqual(_command_name_from_text("/copy-context"), "copy_context")
        self.assertEqual(_command_name_from_text("/multiline-mode"), "multiline_mode")

    def test_git_commands_are_denied(self):
        for name in ("commit", "git", "undo", "diff"):
            self.assertIn(name, REVIEW_MODE_DENIED_COMMANDS)
        self.assertNotIn("run", REVIEW_MODE_DENIED_COMMANDS)


class RunPromptCommandTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_command_skips_apply_at_mentions(self):
        session, _conn = await self._make_session()
        with self._validate_ok():
            with patch("acp_server.session.apply_at_mentions") as apply_mock:
                with patch.object(session.coder, "run"):
                    stop_reason = await session.run_prompt("/ls")
        self.assertEqual(stop_reason, "end_turn")
        apply_mock.assert_not_called()

    async def test_denylist_blocks_commit_without_run(self):
        session, conn = await self._make_session()
        run_called = False

        def run(with_message=None):
            nonlocal run_called
            run_called = True

        session.coder.run = run
        with self._validate_ok():
            stop_reason = await session.run_prompt("/commit")
        self.assertEqual(stop_reason, "end_turn")
        self.assertFalse(run_called)
        self.assertTrue(
            any(
                "deaktiviert" in getattr(u.content, "text", "")
                for u in conn.updates
                if getattr(u, "session_update", None) == "agent_message_chunk"
            )
        )

    async def test_run_is_passed_through(self):
        session, _conn = await self._make_session()
        run_called = False

        def run(with_message=None):
            nonlocal run_called
            run_called = True

        session.coder.run = run
        with self._validate_ok():
            stop_reason = await session.run_prompt("/run ls")
        self.assertEqual(stop_reason, "end_turn")
        self.assertTrue(run_called)

    async def test_switch_coder_updates_model_and_skips_llm_turn(self):
        session, conn = await self._make_session()
        new_coder = FakeCoder()
        new_coder.main_model = FakeModel("gpt-4.1")
        new_coder.weak_model = FakeModel("gpt-4o-mini")
        new_coder.editor_model = FakeModel("gpt-4.1")

        session.coder = FakeCoder(
            run_side_effect=SwitchCoder(main_model=FakeModel("gpt-4.1"))
        )

        with self._validate_ok():
            with patch(
                "acp_server.session.switch_coder", return_value=new_coder
            ) as switch_mock:
                stop_reason = await session.run_prompt("/model gpt-4.1")

        switch_mock.assert_called_once()
        self.assertEqual(stop_reason, "end_turn")
        self.assertEqual(session.current_model_id, "gpt-4.1")
        self.assertIs(session.coder, new_coder)
        chunks = [
            u
            for u in conn.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        ]
        self.assertTrue(any("Model: gpt-4.1" in u.content.text for u in chunks))

    async def test_command_before_coder_initializes_coder(self):
        session, conn = await self._make_session()
        session.coder = None
        fake_coder = FakeCoder()

        async def init_coder(model_name=None):
            session.coder = fake_coder

        with self._validate_ok():
            with patch.object(
                session, "initialize_coder", side_effect=init_coder
            ) as init_mock:
                with patch.object(fake_coder, "run"):
                    stop_reason = await session.run_prompt("/ls")

        init_mock.assert_awaited_once()
        self.assertEqual(stop_reason, "end_turn")


class AvailableCommandsTests(unittest.IsolatedAsyncioTestCase):
    async def _new_session(self, agent):
        env = {
            "AIDER_ACP_MODELS": "gpt-4o",
            "OPENAI_API_KEY": "sk-test",
        }
        with patch.dict(os.environ, env, clear=False):
            def validate(self):
                return fake_validate_environment(self.name, {"OPENAI_API_KEY"})(self)

            with patch("acp_server.model_catalog.Model.validate_environment", validate):
                return await agent.new_session(cwd=tempfile.gettempdir())

    def _command_updates(self, conn):
        return [
            u
            for u in conn.updates
            if getattr(u, "session_update", None) == "available_commands_update"
        ]

    async def test_new_session_does_not_send_commands_synchronously(self):
        agent = AiderAgent()
        conn = FakeConn()
        agent.client = conn
        block = asyncio.Event()

        async def blocked_sleep(_delay):
            await block.wait()

        with patch("acp_server.server.asyncio.sleep", blocked_sleep):
            response = await self._new_session(agent)
            self.assertEqual(self._command_updates(conn), [])
            session = agent.sessions[response.session_id]
            await session.advertise_commands()
            block.set()
            await asyncio.sleep(0)

        updates = self._command_updates(conn)
        self.assertEqual(len(updates), 1)
        names = {c.name for c in updates[0].available_commands}
        expected = {c.name for c in curated_available_commands()}
        self.assertEqual(names, expected)
        for denied in ("commit", "git", "diff", "undo", "quit"):
            self.assertNotIn(denied, names)
        self.assertIn("run", names)
        self.assertIn("help", names)
        self.assertNotIn("save", names)
        self.assertNotIn("map", names)

    async def test_first_prompt_advertises_commands_if_delayed_update_missed(self):
        agent = AiderAgent()
        conn = FakeConn()
        agent.client = conn
        block = asyncio.Event()

        async def blocked_sleep(_delay):
            await block.wait()

        def validate_ok(self):
            self.missing_keys = []
            self.keys_in_environment = True
            return {"keys_in_environment": True, "missing_keys": []}

        with patch("acp_server.server.asyncio.sleep", blocked_sleep):
            response = await self._new_session(agent)
            session = agent.sessions[response.session_id]
            session.coder = FakeCoder()
            with patch(
                "aider_bridge.factory.Model.validate_environment", validate_ok
            ):
                with patch.object(session.coder, "run"):
                    await session.run_prompt("hello")
                    self.assertEqual(len(self._command_updates(conn)), 1)
                    self.assertTrue(session.commands_advertised)
                    await session.run_prompt("hello again")
            self.assertEqual(len(self._command_updates(conn)), 1)
            block.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        self.assertEqual(len(self._command_updates(conn)), 1)

    def test_curated_commands_include_model_switchers(self):
        names = {c.name for c in curated_available_commands()}
        self.assertTrue({"model", "weak-model", "editor-model", "models"}.issubset(names))
        self.assertTrue({"run", "web", "chat-mode", "help"}.issubset(names))
        self.assertTrue(names.isdisjoint({"commit", "git", "undo", "diff", "quit", "save", "load", "map"}))


class TransparencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_coder_sends_model_announcement(self):
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
        fake_coder = FakeCoder()

        with patch("acp_server.session.create_coder", return_value=fake_coder):
            with patch("aider_bridge.factory.Model.validate_environment") as validate:
                validate.return_value = {
                    "keys_in_environment": True,
                    "missing_keys": [],
                }
                with patch("aider_bridge.factory.sanity_check_model"):
                    await session.initialize_coder()
        await asyncio.sleep(0.05)

        chunks = [
            u
            for u in conn.updates
            if getattr(u, "session_update", None) == "agent_message_chunk"
        ]
        self.assertTrue(any("Model: gpt-4o" in u.content.text for u in chunks))
        self.assertTrue(any("weak: gpt-4o-mini" in u.content.text for u in chunks))


if __name__ == "__main__":
    unittest.main()
