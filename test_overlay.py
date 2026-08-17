import os
import tempfile
import threading
import unittest
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
        texts = [
            getattr(getattr(u, "content", None), "text", "") for u in conn.updates
        ]
        self.assertTrue(any("thinking" in t for t in texts))
        self.assertFalse(any(t.startswith("Tokens:") for t in texts))
        self.assertFalse(any("Applied edit" in t for t in texts))

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

    def test_default_settings_skip_logs_and_hidden_files(self):
        from aider_bridge.workspace_files import iter_workspace_relative_files

        with tempfile.TemporaryDirectory() as root:
            Path(root, "hello.py").write_text("print('hi')\n")
            Path(root, "aider_acp.log").write_text("noise\n")
            Path(root, "notes.log").write_text("noise\n")
            Path(root, ".hidden").write_text("secret\n")

            class FakeCoder:
                repo = None

            files = iter_workspace_relative_files(root, FakeCoder())
            self.assertEqual(files, ["hello.py"])

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

    def test_project_settings_add_ignore_patterns(self):
        from aider_bridge.workspace_files import iter_workspace_relative_files

        with tempfile.TemporaryDirectory() as root:
            Path(root, "hello.py").write_text("print('hi')\n")
            Path(root, "scratch.tmp").write_text("tmp\n")
            Path(root, ".aider_acp.toml").write_text(
                "[workspace]\nignore = [\"*.tmp\"]\n"
            )

            class FakeCoder:
                repo = None

            files = iter_workspace_relative_files(root, FakeCoder())
            self.assertEqual(files, ["hello.py"])

    def test_ignore_comes_from_settings_not_hardcoded_suffixes(self):
        from aider_bridge.ignore import WorkspaceIgnore
        from aider_bridge.settings import WorkspaceSettings
        from aider_bridge.workspace_files import iter_workspace_relative_files

        with tempfile.TemporaryDirectory() as root:
            Path(root, "hello.py").write_text("print('hi')\n")
            Path(root, "notes.log").write_text("keep me\n")
            Path(root, "scratch.tmp").write_text("skip me\n")
            settings = WorkspaceSettings(
                skip_dotfiles=True,
                honor_gitignore=False,
                honor_aiderignore=False,
                ignore=["*.tmp"],
            )
            ignore = WorkspaceIgnore.from_settings(settings, root)

            class FakeCoder:
                repo = None

            files = iter_workspace_relative_files(root, FakeCoder(), ignore=ignore)
            self.assertEqual(files, ["hello.py", "notes.log"])


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
