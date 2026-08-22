import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Optional, Any

StopReason = Literal["end_turn", "cancelled"]
from aider.commands import SwitchCoder
from aider.coders import Coder
from acp.helpers import update_available_commands, update_current_mode
from acp.schema import ClientCapabilities
from aider_bridge.file_mentions import apply_at_mentions, resolve_command_file_args
from aider_bridge.io_bridge import ACPIO
from aider_bridge.factory import create_coder, check_model_keys, switch_coder
from .available_commands import DENIED_COMMANDS, curated_available_commands
from .session_modes import (
    SESSION_MODES,
    VALID_MODE_IDS,
    aider_edit_format,
    infer_mode_id,
)


REVIEW_MODE_DENIED_COMMANDS = DENIED_COMMANDS
# Slash commands whose args are file paths; Zed attachments arrive as ACP
# resource blocks, not text, so we splice resolved paths into the command text.
FILE_ARG_COMMANDS = frozenset({"add", "drop", "read_only"})
# Slash commands that still send a prompt to the LLM. @-mentions and ACP
# resource attachments must be added to chat context before the command runs.
CHAT_PROMPT_COMMANDS = frozenset({"ask", "code", "architect", "context", "help"})


def _normalize_command_name(name: str) -> str:
    return name.replace("-", "_")


def _is_command(text: str) -> bool:
    return text.lstrip().startswith(("/", "!"))


def _command_name_from_text(text: str) -> str | None:
    stripped = text.lstrip()
    if not stripped:
        return None
    if stripped.startswith("!"):
        return "run"
    if stripped.startswith("/"):
        first_word = stripped.split()[0]
        return _normalize_command_name(first_word[1:])
    return None


def _format_mode_announcement(mode_id: str) -> str:
    for mode in SESSION_MODES:
        if mode.id == mode_id:
            return f"Mode: {mode.name}\n\n"
    return f"Mode: {mode_id}\n\n"


def _format_model_announcement(coder: Coder) -> str:
    main = coder.main_model.name
    extras: list[str] = []
    weak = getattr(coder, "weak_model", None)
    if weak is not None and getattr(weak, "name", None):
        extras.append(f"weak: {weak.name}")
    editor = getattr(coder, "editor_model", None)
    if editor is not None and getattr(editor, "name", None):
        extras.append(f"editor: {editor.name}")
    if extras:
        return f"Model: {main}  ({', '.join(extras)})\n\n"
    return f"Model: {main}\n\n"


def _fs_flag(capabilities: Optional[ClientCapabilities], name: str) -> bool:
    if capabilities is None:
        return False
    fs = getattr(capabilities, "fs", None)
    if fs is None:
        return False
    return bool(getattr(fs, name, False))


class AiderSession:
    def __init__(
        self, 
        session_id: str, 
        connection: Any, 
        loop: asyncio.AbstractEventLoop, 
        cwd: str,
        additional_directories: Optional[list[str]] = None,
        mcp_servers: Optional[list[Any]] = None,
        client_capabilities: Optional[ClientCapabilities] = None,
        available_model_ids: Optional[list[str]] = None,
        current_model_id: Optional[str] = None,
    ):
        self.logger = logging.getLogger(__name__)
        self.session_id = session_id
        self.connection = connection
        self.loop = loop
        self.cwd = os.path.abspath(cwd)
        self.additional_directories = additional_directories or []
        self.mcp_servers = mcp_servers or []
        write_via_client = _fs_flag(client_capabilities, "write_text_file")
        read_via_client = _fs_flag(client_capabilities, "read_text_file")
        if not write_via_client:
            self.logger.warning(
                "Client did not advertise fs.writeTextFile; overlay will flush to disk"
            )
        self.cancelled = threading.Event()
        self.io = ACPIO(
            session_id=session_id,
            connection=connection,
            loop=loop,
            root=self.cwd,
            write_via_client=write_via_client,
            read_via_client=read_via_client,
            cancelled_event=self.cancelled,
        )
        self.coder: Optional[Coder] = None
        self.available_model_ids = list(available_model_ids or [])
        self.current_model_id = current_model_id
        self.current_mode_id = "code"
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._prompt_running = False
        self.commands_advertised = False
        self.logger.debug(
            "[paths] session init session_id=%s cwd=%s io.root=%s additional_directories=%r",
            session_id,
            self.cwd,
            self.io.root,
            self.additional_directories,
        )

    async def initialize_coder(self, model_name: Optional[str] = None):
        model_name = model_name or self.current_model_id
        if not model_name:
            raise RuntimeError("No model selected for this session")
        self.logger.info("Initializing coder with model: %s", model_name)
        
        def _init():
            self.logger.debug("Running coder initialization in executor")
            import sys
            import contextlib

            self.logger.debug(
                "[paths] initialize_coder session_id=%s session.cwd=%s getcwd=%s",
                self.session_id,
                self.cwd,
                os.getcwd(),
            )
            with contextlib.redirect_stdout(sys.stderr):
                self.coder = create_coder(
                    self.io,
                    model_name=model_name,
                    cwd=self.cwd,
                    edit_format=aider_edit_format(self.current_mode_id, model_name),
                )
            self.logger.debug(
                "[paths] initialize_coder done session_id=%s coder.root=%s coder.repo=%s",
                self.session_id,
                self.coder.root,
                self.coder.repo,
            )

        await self.loop.run_in_executor(self.executor, _init)
        self._announce_active_model()

    def _announce_active_model(self) -> None:
        if not self.coder:
            return
        self.io.announce(_format_model_announcement(self.coder))

    def _announce_active_mode(self) -> None:
        self.io.announce(_format_mode_announcement(self.current_mode_id))

    def _send_mode_update(self, mode_id: str) -> None:
        if self.connection is None:
            return
        update = update_current_mode(mode_id)
        coro = self.connection.session_update(
            session_id=self.session_id, update=update
        )
        try:
            if asyncio.get_running_loop() is self.loop:
                self.loop.create_task(coro)
                return
        except RuntimeError:
            pass
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def advertise_commands(self) -> None:
        """Tell Zed which slash commands exist. Safe to call more than once."""
        if self.commands_advertised or self.connection is None:
            return
        await self.connection.session_update(
            session_id=self.session_id,
            update=update_available_commands(curated_available_commands()),
        )
        self.commands_advertised = True
        self.logger.info(
            "Advertised %d slash commands for session %s",
            len(curated_available_commands()),
            self.session_id,
        )

    async def set_model(self, model_id: str) -> None:
        if self._prompt_running:
            raise RuntimeError("Cannot change model while a prompt is running")
        if model_id not in self.available_model_ids:
            raise ValueError(f"Unknown model: {model_id}")

        self.current_model_id = model_id
        if not self.coder:
            return

        def _switch():
            import sys
            import contextlib

            with contextlib.redirect_stdout(sys.stderr):
                self.coder = create_coder(
                    self.io,
                    model_name=model_id,
                    cwd=self.cwd,
                    from_coder=self.coder,
                )

        await self.loop.run_in_executor(self.executor, _switch)

    async def set_mode(self, mode_id: str) -> None:
        if self._prompt_running:
            raise RuntimeError("Cannot change mode while a prompt is running")
        if mode_id not in VALID_MODE_IDS:
            raise ValueError(f"Unknown mode: {mode_id}")

        self.current_mode_id = mode_id
        if self.coder:

            def _switch():
                import sys
                import contextlib

                with contextlib.redirect_stdout(sys.stderr):
                    self.coder = switch_coder(
                        self.io,
                        self.coder,
                        self.cwd,
                        edit_format=aider_edit_format(
                            mode_id, self.current_model_id
                        ),
                    )

            await self.loop.run_in_executor(self.executor, _switch)

        if self.connection is not None:
            await self.connection.session_update(
                session_id=self.session_id,
                update=update_current_mode(mode_id),
            )
        self._announce_active_mode()


    @property
    def prompt_running(self) -> bool:
        return self._prompt_running

    def cancel(self) -> None:
        self.logger.info("Cancelling session %s", self.session_id)
        self.cancelled.set()
        self.loop.call_soon_threadsafe(self.io.signal_async_cancel)

    def _reset_cancel_state(self) -> None:
        self.cancelled.clear()
        self.io.reset_async_cancel()

    async def run_prompt(
        self, prompt_text: str, resource_names: Optional[list[str]] = None
    ) -> StopReason:
        if self._prompt_running:
            raise RuntimeError("Prompt already running for this session")

        self._prompt_running = True
        self._reset_cancel_state()
        try:
            self.logger.debug("Running prompt with text: %s", prompt_text)
            self.logger.debug("Resource names: %s", resource_names)

            await self.advertise_commands()

            if not check_model_keys(self.io, self.current_model_id):
                return "end_turn"

            if not self.coder:
                self.logger.debug("Coder not initialized, initializing now")
                await self.initialize_coder()

            if _is_command(prompt_text) and resource_names:
                cmd_name = _command_name_from_text(prompt_text)
                if cmd_name in FILE_ARG_COMMANDS:
                    abs_paths = resolve_command_file_args(
                        resource_names, self.cwd, self.additional_directories, self.io
                    )
                    if abs_paths:
                        quoted = " ".join(f'"{p}"' for p in abs_paths)
                        prompt_text = f"{prompt_text.rstrip()} {quoted}".strip()

            def _run() -> StopReason:
                import sys
                import contextlib

                self.logger.debug(
                    "[paths] run_prompt session_id=%s session.cwd=%s coder.root=%s getcwd=%s",
                    self.session_id,
                    self.cwd,
                    self.coder.root if self.coder else None,
                    os.getcwd(),
                )
                with contextlib.redirect_stdout(sys.stderr):
                    is_command = _is_command(prompt_text)
                    cmd_name = (
                        _command_name_from_text(prompt_text) if is_command else None
                    )
                    if is_command:
                        if cmd_name in REVIEW_MODE_DENIED_COMMANDS:
                            display = prompt_text.lstrip().split()[0]
                            self.io.tool_error(
                                f"{display} is disabled in review mode "
                                "(git and host-only commands such as clipboard, editor, or exit)."
                            )
                            return "end_turn"
                    if (not is_command) or cmd_name in CHAT_PROMPT_COMMANDS:
                        apply_at_mentions(
                            self.coder,
                            prompt_text,
                            self.cwd,
                            self.additional_directories,
                            extra_mentions=resource_names,
                        )
                    # Empty ACP prompts (Zed @-pills with no typed text) must
                    # not call Coder.run(""); that blocks forever in get_input.
                    if not (prompt_text or "").strip():
                        if self.cancelled.is_set():
                            self.io.clear_overlay()
                            return "cancelled"
                        return "end_turn"
                    self.logger.debug("Running coder with message: %s", prompt_text)
                    try:
                        self.coder.run(with_message=prompt_text)
                    except SwitchCoder as switch:
                        self.coder = switch_coder(
                            self.io, self.coder, self.cwd, **switch.kwargs
                        )
                        self.current_model_id = self.coder.main_model.name
                        show_announcements = (
                            switch.kwargs.get("show_announcements") is not False
                        )
                        if show_announcements:
                            self._announce_active_model()
                        inferred = infer_mode_id(self.coder)
                        if inferred is not None and inferred != self.current_mode_id:
                            self.current_mode_id = inferred
                            self._send_mode_update(inferred)
                            if show_announcements:
                                self._announce_active_mode()
                        return "end_turn"
                    self.logger.debug("Coder run completed")
                    if self.cancelled.is_set():
                        self.io.clear_overlay()
                        return "cancelled"
                    if is_command:
                        if self.io.pending_writes():
                            flushed = self.io.flush_pending_writes()
                            self.logger.info(
                                "[overlay] flushed %d file(s)", len(flushed)
                            )
                        return "end_turn"
                    flushed = self.io.flush_pending_writes()
                    self.logger.info("[overlay] flushed %d file(s)", len(flushed))
                    self._send_usage()
                    return "end_turn"

            return await self.loop.run_in_executor(self.executor, _run)
        finally:
            self._prompt_running = False

    def _send_usage(self) -> None:
        if not self.coder:
            return
        used = int(getattr(self.coder, "total_tokens_sent", 0) or 0)
        info = getattr(self.coder.main_model, "info", None) or {}
        size = int(info.get("max_input_tokens") or 0)
        cost = getattr(self.coder, "total_cost", None)
        self.io.send_usage(used=used, size=size, cost_usd=cost)

    def close(self):
        self.logger.info("Closing session")
        self.cancel()
        self.executor.shutdown(wait=False)
