import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Any
from aider.coders import Coder
from aider_bridge.io_bridge import ACPIO
from aider_bridge.factory import create_coder

class AiderSession:
    def __init__(self, session_id: str, connection: Any, loop: asyncio.AbstractEventLoop, cwd: str):
        self.session_id = session_id
        self.connection = connection
        self.loop = loop
        self.cwd = cwd
        self.io = ACPIO(session_id=session_id, connection=connection, loop=loop, root=cwd)
        self.coder: Optional[Coder] = None
        self.executor = ThreadPoolExecutor(max_workers=1)

    async def initialize_coder(self, model_name: str = "gpt-4o"):
        # Coder creation might do some IO, better to run in executor or at least keep it async-friendly
        def _init():
            import os
            import sys
            import contextlib
            os.chdir(self.cwd)
            with contextlib.redirect_stdout(sys.stderr):
                self.coder = create_coder(self.io, model_name=model_name)

        await self.loop.run_in_executor(self.executor, _init)


    async def run_prompt(self, prompt_text: str):
        if not self.coder:
            await self.initialize_coder()
        
        # coder.run is blocking, so we run it in the executor
        def _run():
            import sys
            import contextlib
            # Redirect stdout to stderr during aider run to avoid corrupting the ACP stream
            with contextlib.redirect_stdout(sys.stderr):
                # Aider's coder.run expects to read from io.get_input if with_message is None.
                # We use run_one or run(with_message=...) to avoid the input loop.
                self.coder.run(with_message=prompt_text)

        await self.loop.run_in_executor(self.executor, _run)

    def close(self):
        self.executor.shutdown(wait=False)
