import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from acp_server.logging_config import configure_logging, parse_log_level


def _file_and_stderr_handlers():
    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    stderr_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        and getattr(h, "stream", None) is sys.stderr
    ]
    return file_handlers, stderr_handlers


class ParseLogLevelTests(unittest.TestCase):
    def test_unset_is_warning(self):
        self.assertEqual(parse_log_level(None), logging.WARNING)

    def test_empty_is_warning(self):
        self.assertEqual(parse_log_level(""), logging.WARNING)
        self.assertEqual(parse_log_level("  "), logging.WARNING)

    def test_warning(self):
        self.assertEqual(parse_log_level("warning"), logging.WARNING)
        self.assertEqual(parse_log_level("WARNING"), logging.WARNING)

    def test_debug(self):
        self.assertEqual(parse_log_level("debug"), logging.DEBUG)
        self.assertEqual(parse_log_level("DEBUG"), logging.DEBUG)

    def test_off(self):
        self.assertIsNone(parse_log_level("off"))
        self.assertIsNone(parse_log_level("OFF"))

    def test_invalid_falls_back_to_warning(self):
        self.assertEqual(parse_log_level("verbose"), logging.WARNING)
        self.assertEqual(parse_log_level("info"), logging.WARNING)


class ConfigureLoggingTests(unittest.TestCase):
    def setUp(self):
        self._root = logging.getLogger()
        self._saved_handlers = list(self._root.handlers)
        self._saved_level = self._root.level
        self._saved_disable = logging.root.manager.disable
        self._tmpdir = tempfile.TemporaryDirectory()
        self.log_path = Path(self._tmpdir.name) / "aider_acp.log"

    def tearDown(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in self._saved_handlers:
            root.addHandler(handler)
        root.setLevel(self._saved_level)
        logging.disable(self._saved_disable)
        self._tmpdir.cleanup()

    def test_warning_uses_file_and_stderr(self):
        with patch.dict(os.environ, {"AIDER_ACP_LOG_LEVEL": "warning"}):
            configure_logging(self.log_path)
        root = logging.getLogger()
        self.assertEqual(root.level, logging.WARNING)
        file_handlers, stderr_handlers = _file_and_stderr_handlers()
        self.assertEqual(len(file_handlers), 1)
        self.assertEqual(len(stderr_handlers), 1)
        self.assertEqual(
            Path(file_handlers[0].baseFilename), self.log_path.resolve()
        )

    def test_unset_uses_warning_file_and_stderr(self):
        with patch.dict(os.environ):
            os.environ.pop("AIDER_ACP_LOG_LEVEL", None)
            configure_logging(self.log_path)
        root = logging.getLogger()
        self.assertEqual(root.level, logging.WARNING)
        file_handlers, stderr_handlers = _file_and_stderr_handlers()
        self.assertEqual(len(file_handlers), 1)
        self.assertEqual(len(stderr_handlers), 1)

    def test_debug_sets_debug_level(self):
        with patch.dict(os.environ, {"AIDER_ACP_LOG_LEVEL": "debug"}):
            configure_logging(self.log_path)
        root = logging.getLogger()
        self.assertEqual(root.level, logging.DEBUG)
        file_handlers, stderr_handlers = _file_and_stderr_handlers()
        self.assertEqual(len(file_handlers), 1)
        self.assertEqual(len(stderr_handlers), 1)

    def test_off_has_no_file_or_stderr_handlers(self):
        with patch.dict(os.environ, {"AIDER_ACP_LOG_LEVEL": "off"}):
            configure_logging(self.log_path)
        file_handlers, stderr_handlers = _file_and_stderr_handlers()
        self.assertEqual(file_handlers, [])
        self.assertEqual(stderr_handlers, [])
        self.assertFalse(self.log_path.exists())

    def test_invalid_falls_back_to_warning(self):
        with patch.dict(os.environ, {"AIDER_ACP_LOG_LEVEL": "verbose"}):
            configure_logging(self.log_path)
        root = logging.getLogger()
        self.assertEqual(root.level, logging.WARNING)
        file_handlers, stderr_handlers = _file_and_stderr_handlers()
        self.assertEqual(len(file_handlers), 1)
        self.assertEqual(len(stderr_handlers), 1)
        for handler in root.handlers:
            handler.flush()
        self.assertTrue(self.log_path.exists())
        self.assertIn("AIDER_ACP_LOG_LEVEL", self.log_path.read_text())


if __name__ == "__main__":
    unittest.main()
