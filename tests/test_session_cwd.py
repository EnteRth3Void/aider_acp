import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from acp_server.session import AiderSession
from aider.utils import safe_abs_path
from test_models import FakeConn


class FakeModel:
    def __init__(self, name: str = "gpt-4o"):
        self.name = name

    def commit_message_models(self):
        return []


class FakeCoder:
    root = tempfile.gettempdir()
    repo = None

    def __init__(self):
        self.main_model = FakeModel()
        self.weak_model = FakeModel("gpt-4o-mini")
        self.editor_model = FakeModel("gpt-4o")
        self.abs_root_path_cache = {}

    def run(self, with_message=None):
        pass


class SessionCwdIsolationTests(unittest.IsolatedAsyncioTestCase):
    def _validate_ok(self):
        def validate(self):
            self.missing_keys = []
            self.keys_in_environment = True
            return {"keys_in_environment": True, "missing_keys": []}

        return patch("aider_bridge.factory.Model.validate_environment", validate)

    async def test_initialize_coder_does_not_change_getcwd(self):
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as cwd_a, tempfile.TemporaryDirectory() as cwd_b:
            loop = asyncio.get_running_loop()
            session_a = AiderSession(
                session_id="a",
                connection=FakeConn(),
                loop=loop,
                cwd=cwd_a,
                available_model_ids=["gpt-4o"],
                current_model_id="gpt-4o",
            )
            session_b = AiderSession(
                session_id="b",
                connection=FakeConn(),
                loop=loop,
                cwd=cwd_b,
                available_model_ids=["gpt-4o"],
                current_model_id="gpt-4o",
            )
            fake_coder = FakeCoder()
            with self._validate_ok():
                with patch(
                    "acp_server.session.create_coder", return_value=fake_coder
                ):
                    with patch("aider_bridge.factory.sanity_check_model"):
                        await session_a.initialize_coder()
                        self.assertEqual(os.getcwd(), original)
                        await session_b.initialize_coder()
                        self.assertEqual(os.getcwd(), original)

    async def test_run_prompt_does_not_change_getcwd(self):
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as cwd_a, tempfile.TemporaryDirectory() as cwd_b:
            loop = asyncio.get_running_loop()
            session_a = AiderSession(
                session_id="a",
                connection=FakeConn(),
                loop=loop,
                cwd=cwd_a,
                available_model_ids=["gpt-4o"],
                current_model_id="gpt-4o",
            )
            session_b = AiderSession(
                session_id="b",
                connection=FakeConn(),
                loop=loop,
                cwd=cwd_b,
                available_model_ids=["gpt-4o"],
                current_model_id="gpt-4o",
            )
            session_a.coder = FakeCoder()
            session_b.coder = FakeCoder()
            with self._validate_ok():
                with patch.object(session_a.coder, "run"):
                    await session_a.run_prompt("hello")
                    self.assertEqual(os.getcwd(), original)
                with patch.object(session_b.coder, "run"):
                    await session_b.run_prompt("hello again")
                    self.assertEqual(os.getcwd(), original)


class FinalizeCoderRootTests(unittest.TestCase):
    def _init_git_repo(self, root: str) -> None:
        subprocess.run(
            ["git", "init"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        hello = Path(root) / "hello.py"
        hello.write_text("print('hi')\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "hello.py"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=root,
            check=True,
            capture_output=True,
        )

    def test_finalize_coder_binds_root_and_repo_to_session_cwd(self):
        from aider_bridge.factory import _finalize_coder

        adapter_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: None)
        project_dir = tempfile.mkdtemp()
        self._init_git_repo(project_dir)
        original = os.getcwd()
        os.chdir(adapter_dir)
        self.addCleanup(os.chdir, original)

        coder = FakeCoder()
        coder.check_for_file_mentions = lambda content: None
        coder.get_file_mentions = lambda content: []
        coder.get_inchat_relative_files = lambda: []
        io = type("FakeIO", (), {"root": project_dir})()

        _finalize_coder(coder, io, cwd=project_dir, from_coder=None)

        self.assertEqual(coder.root, safe_abs_path(project_dir))
        self.assertIsNotNone(coder.repo)
        self.assertEqual(coder.repo.root, safe_abs_path(project_dir))
        self.assertNotEqual(coder.root, safe_abs_path(adapter_dir))

    def test_create_coder_finalize_binds_root_to_passed_cwd(self):
        from aider_bridge.factory import create_coder

        adapter_dir = tempfile.mkdtemp()
        project_dir = tempfile.mkdtemp()
        self._init_git_repo(project_dir)
        original = os.getcwd()
        os.chdir(adapter_dir)
        self.addCleanup(os.chdir, original)

        created = FakeCoder()
        created.check_for_file_mentions = lambda content: None
        created.get_file_mentions = lambda content: []
        created.get_inchat_relative_files = lambda: []
        io = type("FakeIO", (), {"root": project_dir})()

        with patch("aider_bridge.factory.patch_run_cmd"):
            with patch("aider_bridge.factory.patch_coder_file_mentions"):
                with patch("aider_bridge.factory.sanity_check_model"):
                    with patch("aider_bridge.factory.Model") as model_cls:
                        model_cls.return_value = MagicMock()
                        with patch(
                            "aider_bridge.factory.Coder.create", return_value=created
                        ):
                            coder = create_coder(
                                io,
                                model_name="gpt-4o",
                                cwd=project_dir,
                            )

        self.assertEqual(coder.root, safe_abs_path(project_dir))
        self.assertIsNotNone(coder.repo)
        self.assertEqual(coder.repo.root, safe_abs_path(project_dir))


class RunCmdCwdTests(unittest.TestCase):
    def setUp(self):
        import aider.run_cmd as run_cmd_module

        self.run_cmd_module = run_cmd_module
        self._saved_subprocess = run_cmd_module.run_cmd_subprocess
        self._saved_pexpect = run_cmd_module.run_cmd_pexpect
        if hasattr(run_cmd_module.run_cmd_subprocess, "_acp_patched"):
            delattr(run_cmd_module.run_cmd_subprocess, "_acp_patched")
        if hasattr(run_cmd_module.run_cmd_pexpect, "_acp_patched"):
            delattr(run_cmd_module.run_cmd_pexpect, "_acp_patched")

    def tearDown(self):
        self.run_cmd_module.run_cmd_subprocess = self._saved_subprocess
        self.run_cmd_module.run_cmd_pexpect = self._saved_pexpect

    def test_run_cmd_subprocess_forwards_io_root_when_cwd_missing(self):
        captured = {}

        def fake_subprocess(command, verbose=False, cwd=None, encoding=None):
            captured["cwd"] = cwd
            return 0, "ok"

        io_root = tempfile.mkdtemp()
        io = type(
            "FakeIO",
            (),
            {"root": io_root, "command_output": lambda self, output, command=None: None},
        )()

        with patch.object(self.run_cmd_module, "run_cmd_subprocess", fake_subprocess):
            with patch.object(self.run_cmd_module, "run_cmd_pexpect", MagicMock()):
                from aider_bridge.shell_commands import patch_run_cmd

                patch_run_cmd(io)
                exit_status, output = self.run_cmd_module.run_cmd_subprocess("echo hi")
                self.assertEqual(exit_status, 0)
                self.assertEqual(output, "ok")
                self.assertEqual(captured["cwd"], io_root)


if __name__ == "__main__":
    unittest.main()
