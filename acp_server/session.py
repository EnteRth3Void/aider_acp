import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Any
from aider.coders import Coder
from aider_bridge.file_mentions import apply_at_mentions
from aider_bridge.io_bridge import ACPIO
from aider_bridge.factory import create_coder

class AiderSession:
    def __init__(
        self, 
        session_id: str, 
        connection: Any, 
        loop: asyncio.AbstractEventLoop, 
        cwd: str,
        additional_directories: Optional[list[str]] = None,
        mcp_servers: Optional[list[Any]] = None
    ):
        self.logger = logging.getLogger(__name__)
        self.session_id = session_id
        self.connection = connection
        self.loop = loop
        self.cwd = os.path.abspath(cwd)
        self.additional_directories = additional_directories or []
        self.mcp_servers = mcp_servers or []
        self.io = ACPIO(
            session_id=session_id, connection=connection, loop=loop, root=self.cwd
        )
        self.coder: Optional[Coder] = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.logger.info(
            "[paths] session init session_id=%s cwd=%s io.root=%s additional_directories=%r",
            session_id,
            self.cwd,
            self.io.root,
            self.additional_directories,
        )

    async def initialize_coder(self, model_name: str = "gpt-4o"):
        # Coder creation might do some IO, better to run in executor or at least keep it async-friendly
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


    async def run_prompt(
        self, prompt_text: str, resource_names: Optional[list[str]] = None
    ):
        self.logger.info("Running prompt with text: %s", prompt_text)
        self.logger.debug("Resource names: %s", resource_names)

        if not self.coder:
            self.logger.debug("Coder not initialized, initializing now")
            await self.initialize_coder()

        def _run():
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

        await self.loop.run_in_executor(self.executor, _run)

    def close(self):
        self.logger.info("Closing session")
        self.executor.shutdown(wait=False)
