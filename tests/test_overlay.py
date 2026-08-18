import asyncio
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from aider.utils import safe_abs_path

from aider_bridge.io_bridge import ACPIO, strip_aider_edit_blocks


class FakeConn:
    def __init__(self):
        self.writes: list[tuple[str, str]] = []
        self.updates = []
        self.reads: dict[str, str] = {}

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)

    async def write_text_file(self, content, path, session_id, **kwargs):
        self.writes.append((path, content))
        return None

    async def read_text_file(self, path, session_id, **kwargs):
        if path not in self.reads:
            raise FileNotFoundError(path)

        class _Resp:
            def __init__(self, content):
                self.content = content

        return _Resp(self.reads[path])


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = self._tmpdir.name
        self.loop = asyncio_new_running_loop()
        self.conn = FakeConn()
        self.io = ACPIO(
            session_id="s1",
            connection=self.conn,
            loop=self.loop,
            root=self.root,
            write_via_client=False,
            read_via_client=False,
        )

    def tearDown(self):
        stop_loop(self.loop)
        self._tmpdir.cleanup()

    def _path(self, name: str) -> str:
        return str(safe_abs_path(os.path.join(self.root, name)))

    def test_multiple_writes_coalesce_to_one_pending_file(self):
        path = self._path("foo.py")
        Path(path).write_text("orig\n", encoding="utf-8")

        self.io.write_text(path, "first\n")
        self.io.write_text(path, "second\n")

        self.assertEqual(self.io.read_text(path), "second\n")
        pending = self.io.pending_writes()
        self.assertEqual(len(pending), 1)
        staged_path, old_text, new_text = pending[0]
        self.assertEqual(staged_path, path)
        self.assertEqual(old_text, "orig\n")
        self.assertEqual(new_text, "second\n")
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "orig\n")

    def test_flush_without_client_writes_disk_once(self):
        path = self._path("bar.py")
        Path(path).write_text("old\n", encoding="utf-8")
        self.io.write_text(path, "new\n")
        flushed = self.io.flush_pending_writes()

        self.assertEqual(flushed, [path])
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "new\n")
        self.assertEqual(self.io.pending_writes(), [])

    def test_flush_via_client_sends_one_write_and_diff(self):
        path = self._path("baz.py")
        Path(path).write_text("before\n", encoding="utf-8")
        self.io.write_via_client = True
        self.io.write_text(path, "after\n")
        flushed = self.io.flush_pending_writes()

        self.assertEqual(flushed, [path])
        self.assertEqual(self.conn.writes, [(path, "after\n")])
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "before\n")
        kinds = [
            getattr(u, "session_update", None) or getattr(u, "sessionUpdate", None)
            for u in self.conn.updates
        ]
        self.assertIn("tool_call", kinds)
        self.assertIn("tool_call_update", kinds)

    def test_read_via_client_prefers_overlay_then_client(self):
        path = self._path("qux.py")
        self.conn.reads[path] = "from-zed\n"
        self.io.read_via_client = True
        self.assertEqual(self.io.read_text(path), "from-zed\n")
        self.io.write_text(path, "staged\n")
        self.assertEqual(self.io.read_text(path), "staged\n")


class MessageFilterTests(unittest.TestCase):
    def test_strip_search_replace_keeps_prose(self):
        message = (
            "Ich ändere den String.\n"
            "hello.py\n"
            "<<<<<<< SEARCH\n"
            'print("Hello, World!")\n'
            "=======\n"
            'print("Hallo Peter")\n'
            ">>>>>>> REPLACE\n"
            "Fertig.\n"
        )
        visible = strip_aider_edit_blocks(message)
        self.assertIn("Ich ändere den String.", visible)
        self.assertIn("Fertig.", visible)
        self.assertNotIn("<<<<<<< SEARCH", visible)
        self.assertNotIn("Hello, World!", visible)

    def test_strip_fenced_search_replace(self):
        message = (
            "```python\n"
            "<<<<<<< SEARCH\n"
            "a\n"
            "=======\n"
            "b\n"
            ">>>>>>> REPLACE\n"
            "```\n"
        )
        self.assertEqual(strip_aider_edit_blocks(message), "")

    def test_tool_output_skips_tokens_and_applied_edit(self):
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
        io.tool_output("Tokens: 5.4k sent, 38 received. Cost: $0.01 message, $0.01 session.")
        io.tool_output("Applied edit to hello.py")
        io.tool_output("Something the model is thinking")
        io.tool_output("Added utils.py to the chat")
        kinds_and_texts = [
            (
                getattr(u, "session_update", None) or getattr(u, "sessionUpdate", None),
                getattr(getattr(u, "content", None), "text", ""),
            )
            for u in conn.updates
        ]
        self.assertTrue(any("thinking" in t for _k, t in kinds_and_texts))
        self.assertFalse(any(t.startswith("Tokens:") for _k, t in kinds_and_texts))
        self.assertFalse(any("Applied edit" in t for _k, t in kinds_and_texts))
        added = [
            (kind, text)
            for kind, text in kinds_and_texts
            if "Added utils.py to the chat" in text
        ]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0][0], "agent_message_chunk")

    def _message_chunks(self, conn):
        chunks = []
        for u in conn.updates:
            kind = getattr(u, "session_update", None) or getattr(
                u, "sessionUpdate", None
            )
            if kind == "agent_message_chunk":
                chunks.append(getattr(u.content, "text", ""))
        return chunks

    def _drain_loop(self, loop):
        future = asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop)
        future.result(timeout=2)

    def _edit_block_only(self):
        return (
            "hello.py\n"
            "<<<<<<< SEARCH\n"
            'print("Hello, World!")\n'
            "=======\n"
            'print("Hallo Peter")\n'
            ">>>>>>> REPLACE\n"
        )

    def _fenced_edit_block_only(self):
        return (
            "```python\n"
            "<<<<<<< SEARCH\n"
            "a\n"
            "=======\n"
            "b\n"
            ">>>>>>> REPLACE\n"
            "```\n"
        )

    def test_assistant_output_strips_edit_blocks_keeps_prose(self):
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
        message = (
            "Ich ändere den String.\n"
            + self._edit_block_only()
            + "Fertig.\n"
        )
        io.assistant_output(message)
        self._drain_loop(loop)
        chunks = self._message_chunks(conn)
        self.assertEqual(len(chunks), 1)
        self.assertIn("Ich ändere den String.", chunks[0])
        self.assertIn("Fertig.", chunks[0])
        self.assertNotIn("<<<<<<< SEARCH", chunks[0])

    def test_assistant_output_edit_only_empty_overlay_sends_fallback(self):
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
        message = self._fenced_edit_block_only()
        io.assistant_output(message)
        self._drain_loop(loop)
        chunks = self._message_chunks(conn)
        self.assertEqual(len(chunks), 1)
        self.assertIn("<<<<<<< SEARCH", chunks[0])

    def test_assistant_output_edit_only_with_overlay_sends_nothing(self):
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
        path = str(safe_abs_path(os.path.join(tmp.name, "hello.py")))
        Path(path).write_text("orig\n", encoding="utf-8")
        io.write_text(path, "new\n")
        io.assistant_output(self._fenced_edit_block_only())
        self._drain_loop(loop)
        self.assertEqual(self._message_chunks(conn), [])

    def test_send_usage_emits_usage_update(self):
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
        io.send_usage(used=5400, size=128000, cost_usd=0.01)
        update = conn.updates[-1]
        kind = getattr(update, "session_update", None) or getattr(update, "sessionUpdate", None)
        self.assertEqual(kind, "usage_update")
        self.assertEqual(update.used, 5400)
        self.assertEqual(update.size, 128000)
        self.assertEqual(update.cost.amount, 0.01)


class WorkspaceSkipTests(unittest.TestCase):
    def setUp(self):
        from aider_bridge.ignore import clear_ignore_cache

        clear_ignore_cache()

    def tearDown(self):
        from aider_bridge.ignore import clear_ignore_cache

        clear_ignore_cache()

    def test_basename_walk_skips_hidden_files_not_logs(self):
        from aider_bridge.workspace_files import iter_workspace_relative_files

        with tempfile.TemporaryDirectory() as root:
            Path(root, "hello.py").write_text("print('hi')\n")
            Path(root, "notes.log").write_text("noise\n")
            Path(root, ".hidden").write_text("secret\n")

            class FakeCoder:
                repo = None

            files = iter_workspace_relative_files(root, FakeCoder())
            self.assertEqual(files, ["hello.py", "notes.log"])

    def test_project_gitignore_is_honored_without_git(self):
        from aider_bridge.workspace_files import iter_workspace_relative_files

        with tempfile.TemporaryDirectory() as root:
            Path(root, "hello.py").write_text("print('hi')\n")
            Path(root, "secret.txt").write_text("nope\n")
            Path(root, ".gitignore").write_text("secret.txt\n")

            class FakeCoder:
                repo = None

            files = iter_workspace_relative_files(root, FakeCoder())
            self.assertEqual(files, ["hello.py"])

    def test_aiderignore_is_honored(self):
        from aider_bridge.workspace_files import iter_workspace_relative_files

        with tempfile.TemporaryDirectory() as root:
            Path(root, "hello.py").write_text("print('hi')\n")
            Path(root, "scratch.tmp").write_text("tmp\n")
            Path(root, ".aiderignore").write_text("*.tmp\n")

            class FakeCoder:
                repo = None

            files = iter_workspace_relative_files(root, FakeCoder())
            self.assertEqual(files, ["hello.py"])


class FinalizeCoderTests(unittest.TestCase):
    def test_finalize_coder_does_not_add_workspace_files(self):
        from aider_bridge.factory import _finalize_coder

        with tempfile.TemporaryDirectory() as root:
            Path(root, "hello.py").write_text("print('hi')\n")

            class FakeCoder:
                def __init__(self):
                    self.repo = None
                    self.abs_fnames = set()
                    self.abs_root_path_cache = {}
                    self.ignore_mentions = set()
                    self.add_rel_fname_calls = []

                def add_rel_fname(self, rel):
                    self.add_rel_fname_calls.append(rel)
                    self.abs_fnames.add(rel)

                def get_inchat_relative_files(self):
                    return sorted(self.abs_fnames)

                def check_for_file_mentions(self, content):
                    return None

                def get_file_mentions(self, content):
                    return []

                def abs_root_path(self, rel_fname):
                    return str(Path(self.root) / rel_fname)

            coder = FakeCoder()
            io = type("FakeIO", (), {"root": root})()

            _finalize_coder(coder, io, cwd=root, from_coder=None)

            self.assertEqual(coder.get_inchat_relative_files(), [])
            self.assertEqual(coder.add_rel_fname_calls, [])
            self.assertEqual(coder.root, safe_abs_path(root))


class FakeConnWithPermission(FakeConn):
    async def request_permission(self, options, session_id, tool_call, **kwargs):
        await asyncio.Event().wait()


class PromptLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_blocks_until_run_prompt_finishes(self):
        from acp.schema import TextContentBlock
        from acp_server.server import AiderAgent

        order: list[str] = []

        class SlowSession:
            cwd = "/tmp"
            coder = None
            prompt_running = False

            class _IO:
                root = "/tmp"

            io = _IO()

            async def run_prompt(self, prompt_text, resource_names=None):
                order.append("run_start")
                await asyncio.sleep(0.05)
                order.append("run_end")
                return "end_turn"

        agent = AiderAgent()
        agent.sessions["s1"] = SlowSession()
        order.append("before")
        response = await agent.prompt(
            [TextContentBlock(text="hi", type="text")], session_id="s1"
        )
        order.append("after")

        self.assertEqual(
            order, ["before", "run_start", "run_end", "after"]
        )
        self.assertEqual(response.stop_reason, "end_turn")

    async def test_user_message_id_only_when_provided(self):
        from acp.schema import TextContentBlock
        from acp_server.server import AiderAgent

        class InstantSession:
            cwd = "/tmp"
            coder = None
            prompt_running = False

            class _IO:
                root = "/tmp"

            io = _IO()

            async def run_prompt(self, prompt_text, resource_names=None):
                return "end_turn"

        agent = AiderAgent()
        agent.sessions["s1"] = InstantSession()
        agent.sessions["s2"] = InstantSession()

        with_id = await agent.prompt(
            [TextContentBlock(text="hi", type="text")],
            session_id="s1",
            message_id="msg-123",
        )
        without_id = await agent.prompt(
            [TextContentBlock(text="hi", type="text")], session_id="s2"
        )

        self.assertEqual(with_id.user_message_id, "msg-123")
        self.assertIsNone(without_id.user_message_id)

    async def test_cancel_before_flush_skips_writes(self):
        from acp_server.session import AiderSession

        loop = asyncio.get_running_loop()

        class FakeIO:
            root = "/tmp"

            def __init__(self):
                self.flush_called = False
                self.clear_called = False

            def flush_pending_writes(self):
                self.flush_called = True
                return []

            def clear_overlay(self):
                self.clear_called = True

            def reset_async_cancel(self):
                pass

        class FakeCoder:
            root = "/tmp"

            def __init__(self, session):
                self.session = session

            def run(self, with_message=None):
                self.session.cancelled.set()

        fake_io = FakeIO()
        session = AiderSession(
            session_id="s1",
            connection=FakeConn(),
            loop=loop,
            cwd=tempfile.gettempdir(),
            available_model_ids=["gpt-4o"],
            current_model_id="gpt-4o",
        )
        session.io = fake_io
        session.coder = FakeCoder(session)

        with patch("acp_server.session.check_model_keys", return_value=True):
            stop_reason = await session.run_prompt("hello")

        self.assertEqual(stop_reason, "cancelled")
        self.assertTrue(fake_io.clear_called)
        self.assertFalse(fake_io.flush_called)

    def test_confirm_ask_unblocks_on_cancel(self):
        loop = asyncio_new_running_loop()
        self.addCleanup(lambda: stop_loop(loop))
        cancelled = threading.Event()
        conn = FakeConnWithPermission()
        io = ACPIO(
            session_id="s1",
            connection=conn,
            loop=loop,
            root=tempfile.gettempdir(),
            cancelled_event=cancelled,
        )

        result: list[bool] = []

        def ask():
            result.append(io.confirm_ask("Proceed?"))

        thread = threading.Thread(target=ask)
        thread.start()
        time.sleep(0.1)
        cancelled.set()
        loop.call_soon_threadsafe(io.signal_async_cancel)
        thread.join(timeout=2)

        self.assertEqual(len(result), 1)
        self.assertFalse(result[0])


def asyncio_new_running_loop():
    import asyncio

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


if __name__ == "__main__":
    unittest.main()
