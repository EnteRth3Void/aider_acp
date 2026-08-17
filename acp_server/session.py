import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Optional, Any

StopReason = Literal["end_turn", "cancelled"]
from aider.coders import Coder
from acp.schema import ClientCapabilities
from aider_bridge.file_mentions import apply_at_mentions
from aider_bridge.io_bridge import ACPIO
from aider_bridge.factory import create_coder, check_model_keys


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
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._prompt_running = False
        self.logger.info(
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

            self.logger.info(
                "[paths] initialize_coder before chdir session_id=%s session.cwd=%s getcwd=%s",
                self.session_id,
                self.cwd,
                os.getcwd(),
            )
            os.chdir(self.cwd)
            self.logger.info(
                "[paths] initialize_coder after chdir session_id=%s getcwd=%s io.root=%s",
                self.session_id,
                os.getcwd(),
                self.io.root,
            )
            with contextlib.redirect_stdout(sys.stderr):
                self.coder = create_coder(self.io, model_name=model_name, cwd=self.cwd)
            self.logger.info(
                "[paths] initialize_coder done session_id=%s coder.root=%s coder.repo=%s",
                self.session_id,
                self.coder.root,
                self.coder.repo,
            )

        await self.loop.run_in_executor(self.executor, _init)

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

            os.chdir(self.cwd)
            with contextlib.redirect_stdout(sys.stderr):
                self.coder = create_coder(
                    self.io,
                    model_name=model_id,
                    cwd=self.cwd,
                    from_coder=self.coder,
                )

        await self.loop.run_in_executor(self.executor, _switch)


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
            self.logger.info("Running prompt with text: %s", prompt_text)
            self.logger.debug("Resource names: %s", resource_names)

            if not check_model_keys(self.io, self.current_model_id):
                return "end_turn"

            if not self.coder:
                self.logger.debug("Coder not initialized, initializing now")
                await self.initialize_coder()

            def _run() -> StopReason:
                import sys
                import contextlib

                self.logger.info(
                    "[paths] run_prompt before chdir session_id=%s session.cwd=%s coder.root=%s getcwd=%s",
                    self.session_id,
                    self.cwd,
                    self.coder.root if self.coder else None,
                    os.getcwd(),
                )
                os.chdir(self.cwd)
                self.logger.info(
                    "[paths] run_prompt after chdir session_id=%s getcwd=%s io.root=%s coder.root=%s",
                    self.session_id,
                    os.getcwd(),
                    self.io.root,
                    self.coder.root if self.coder else None,
                )
                with contextlib.redirect_stdout(sys.stderr):
                    apply_at_mentions(
                        self.coder,
                        prompt_text,
                        self.cwd,
                        self.additional_directories,
                        extra_mentions=resource_names,
                    )
                    self.logger.debug("Running coder with message: %s", prompt_text)
                    self.coder.run(with_message=prompt_text)
                    self.logger.debug("Coder run completed")
                    if self.cancelled.is_set():
                        self.io.clear_overlay()
                        return "cancelled"
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
