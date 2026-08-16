import functools
import logging
from typing import TYPE_CHECKING

import aider.run_cmd as run_cmd_module

if TYPE_CHECKING:
    from .io_bridge import ACPIO

logger = logging.getLogger(__name__)


def patch_run_cmd(io: "ACPIO") -> None:
    """Forward shell command stdout to the ACP client.

    Aider's run_cmd prints to stdout (redirected to stderr in ACP sessions) and
    never calls io methods with the captured output.
    """

    if getattr(run_cmd_module.run_cmd_subprocess, "_acp_patched", False):
        return

    original_subprocess = run_cmd_module.run_cmd_subprocess
    original_pexpect = run_cmd_module.run_cmd_pexpect

    @functools.wraps(original_subprocess)
    def run_cmd_subprocess(command, verbose=False, cwd=None, encoding=None):
        import sys

        logger.info("[paths] shell command=%r cwd=%s io.root=%s", command, cwd, io.root)
        if encoding is None:
            encoding = sys.stdout.encoding
        exit_status, output = original_subprocess(
            command, verbose=verbose, cwd=cwd, encoding=encoding
        )
        if output and output.strip():
            io.command_output(output, command=command)
        return exit_status, output

    @functools.wraps(original_pexpect)
    def run_cmd_pexpect(command, verbose=False, cwd=None):
        logger.info("[paths] shell (pexpect) command=%r cwd=%s io.root=%s", command, cwd, io.root)
        exit_status, output = original_pexpect(command, verbose=verbose, cwd=cwd)
        if output and output.strip():
            io.command_output(output, command=command)
        return exit_status, output

    run_cmd_subprocess._acp_patched = True
    run_cmd_pexpect._acp_patched = True
    run_cmd_module.run_cmd_subprocess = run_cmd_subprocess
    run_cmd_module.run_cmd_pexpect = run_cmd_pexpect
