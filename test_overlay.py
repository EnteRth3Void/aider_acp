import os
import tempfile
import threading
import unittest
from pathlib import Path

from aider.utils import safe_abs_path

from aider_bridge.io_bridge import ACPIO


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
