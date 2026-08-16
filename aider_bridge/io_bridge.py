import asyncio
import logging
import uuid
from typing import Any

from acp.agent.connection import AgentSideConnection
from acp.helpers import start_tool_call, text_block, tool_content, update_tool_call
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    PermissionOption,
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
        self.logger = logging.getLogger(__name__)
        self.session_id = session_id
        self.connection = connection
        self.loop = loop
        super().__init__(pretty=False, **kwargs)
        self.logger.info(
            "[paths] ACPIO init session_id=%s root=%s",
            session_id,
            self.root,
        )

    def _send_update(self, update: Any):
        self.logger.debug("Sending update: %s", update)
        self.logger.debug("Preparing to send update: %s", update)
        future = asyncio.run_coroutine_threadsafe(
            self.connection.session_update(session_id=self.session_id, update=update),
            self.loop,
        )
        future.add_done_callback(lambda f: self.logger.debug("Update sent successfully"))

    def ai_output(self, content: str):
        # Aider calls this for the final AI response
        self.logger.info("AI output received: %s", content)
        chunk = AgentMessageChunk(
            content=TextContentBlock(text=content, type="text"),
            session_update="agent_message_chunk",
        )
        self._send_update(chunk)

    def assistant_output(self, message: str, pretty=None):
        # Aider calls this for intermediate/streaming output
        self.logger.info("Assistant output received: %s", message)
        if not message:
            return
        chunk = AgentMessageChunk(
            content=TextContentBlock(text=message, type="text"),
            session_update="agent_message_chunk",
        )
        self._send_update(chunk)

    def command_output(self, output: str, command: str | None = None):
        """Show captured shell command stdout in the ACP chat."""
        output = output.rstrip()
        if not output:
            return
        if command:
            text = f"```\n$ {command}\n{output}\n```"
        else:
            text = f"```\n{output}\n```"
        self.logger.info("Command output (%d chars): %s", len(output), output[:200])
        chunk = AgentMessageChunk(
            content=TextContentBlock(text=text, type="text"),
            session_update="agent_message_chunk",
        )
        self._send_update(chunk)

    def tool_output(self, *messages: Any, **kwargs: Any):
        # Aider calls this for tool logs/thoughts
        self.logger.info("Tool output received: %s", messages)
        content = " ".join(map(str, messages))
        if not content:
            return
        chunk = AgentThoughtChunk(
            content=TextContentBlock(text=content, type="text"),
            session_update="agent_thought_chunk",
        )
        self._send_update(chunk)

    def tool_error(self, message: str = "", strip: bool = True):
        self.logger.error("Tool error: %s", message)
        self.tool_output(f"Error: {message}")

    def tool_warning(self, message: str = "", strip: bool = True):
        self.logger.warning("Tool warning: %s", message)
        self.tool_output(f"Warning: {message}")

    def confirm_ask(self, question: str, **kwargs: Any) -> bool:
        self.logger.info("Confirmation requested: %s", question)
        if self.yes is True:
            return True
        if self.yes is False:
            return False

        subject = kwargs.get("subject")
        display = f"{question}\n{subject}" if subject else question
        tool_call_id = str(uuid.uuid4())

        self._send_update(
            start_tool_call(
                tool_call_id=tool_call_id,
                title=question,
                kind="other",
                status="pending",
                content=[tool_content(text_block(display))],
            )
        )

        options = [
            PermissionOption(option_id="approve", name="Yes", kind="allow_once"),
            PermissionOption(option_id="reject", name="No", kind="reject_once"),
        ]
        tool_call = update_tool_call(tool_call_id, status="pending")

        future = asyncio.run_coroutine_threadsafe(
            self.connection.request_permission(
                options=options,
                session_id=self.session_id,
                tool_call=tool_call,
            ),
            self.loop,
        )

        try:
            response = future.result()
            if response.outcome.outcome == "cancelled":
                approved = False
            elif response.outcome.outcome == "selected":
                selected = next(
                    (o for o in options if o.option_id == response.outcome.option_id),
                    None,
                )
                approved = selected is not None and selected.kind in (
                    "allow_once",
                    "allow_always",
                )
            else:
                approved = False
        except Exception as e:
            self.tool_error(f"Permission request failed: {e}")
            approved = False

        self._send_update(
            update_tool_call(
                tool_call_id, status="completed" if approved else "failed"
            )
        )
        return approved

    # Suppress terminal outputs
    def user_input(self, inp: str, log_only: bool = True):
        pass

    def console_print(self, *args: Any, **kwargs: Any):
        pass
