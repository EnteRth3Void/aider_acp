import asyncio
from typing import Any

from acp.agent.connection import AgentSideConnection
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ContentChunk,
    TextContentBlock,
)
from aider.io import InputOutput


class ACPIO(InputOutput):
    def __init__(
        self,
        session_id: str,
        connection: AgentSideConnection,
        loop: asyncio.AbstractEventLoop,
        **kwargs,
    ):
        super().__init__(pretty=False, **kwargs)
        self.session_id = session_id
        self.connection = connection
        self.loop = loop

    def _send_update(self, update: Any):
        asyncio.run_coroutine_threadsafe(
            self.connection.session_update(session_id=self.session_id, update=update),
            self.loop,
        )

    def ai_output(self, content: str):
        # Aider calls this for the final AI response
        chunk = AgentMessageChunk(
            content=TextContentBlock(text=content, type="text"),
            session_update="agent_message_chunk",
        )
        self._send_update(chunk)

    def assistant_output(self, message: str, pretty=None):
        # Aider calls this for intermediate/streaming output
        if not message:
            return
        chunk = AgentMessageChunk(
            content=TextContentBlock(text=message, type="text"),
            session_update="agent_message_chunk",
        )
        self._send_update(chunk)

    def tool_output(self, *messages: Any, **kwargs: Any):
        # Aider calls this for tool logs/thoughts
        content = " ".join(map(str, messages))
        if not content:
            return
        chunk = AgentThoughtChunk(
            content=TextContentBlock(text=content, type="text"),
            session_update="agent_thought_chunk",
        )
        self._send_update(chunk)

    def tool_error(self, message: str = "", strip: bool = True):
        self.tool_output(f"Error: {message}")

    def tool_warning(self, message: str = "", strip: bool = True):
        self.tool_output(f"Warning: {message}")

    def confirm_ask(self, question: str, **kwargs: Any) -> bool:
        # Initial implementation: auto-approve or block (to be improved in Phase 2)
        # For now, we'll log it as a thought and default to True if self.yes is set
        self.tool_output(f"Confirmation requested: {question}")
        if self.yes:
            return True

        # Phase 2 will implement request_permission here
        return True

    # Suppress terminal outputs
    def user_input(self, inp: str, log_only: bool = True):
        pass

    def console_print(self, *args: Any, **kwargs: Any):
        pass
