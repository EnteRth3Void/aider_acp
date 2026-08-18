import asyncio
import logging
import re
import uuid
from concurrent.futures import Future
from typing import Any, Optional

from acp.agent.connection import AgentSideConnection
from acp.helpers import (
    start_tool_call,
    text_block,
    tool_content,
    tool_diff_content,
    update_tool_call,
)
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    Cost,
    PermissionOption,
    TextContentBlock,
    ToolCallLocation,
    UsageUpdate,
)
from aider.io import InputOutput
from aider.utils import is_image_file, safe_abs_path

# Aider streams SEARCH/REPLACE as assistant text; Zed already gets a proper diff tool_call.
_FENCED_EDIT_RE = re.compile(
    r"```[^\n]*\n[ \t]*<<<<<<< SEARCH\n.*?>>>>>>> REPLACE[ \t]*\n```[ \t]*\n?",
    re.DOTALL,
)
_EDIT_BLOCK_RE = re.compile(
    r"^[ \t]*<<<<<<< SEARCH\n.*?^[ \t]*>>>>>>> REPLACE[ \t]*\n?",
    re.MULTILINE | re.DOTALL,
)
_APPLIED_EDIT_RE = re.compile(
    r"^(Did not apply edit to |Applied edit to )\S+",
    re.IGNORECASE,
)
_TOKENS_RE = re.compile(r"^Tokens:", re.IGNORECASE)
# Aider command status ("Added utils.py to the chat") is user-facing, not thinking.
_CHAT_STATUS_RE = re.compile(
    r"^(Added|Removed|Moved|Dropping|Converted)\b",
    re.IGNORECASE,
)


def strip_aider_edit_blocks(message: str) -> str:
    """Remove Aider SEARCH/REPLACE blocks; keep surrounding prose."""
    if not message:
        return message
    text = _FENCED_EDIT_RE.sub("", message)
    text = _EDIT_BLOCK_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class ACPIO(InputOutput):
    def __init__(
        self,
        session_id: str,
        connection: AgentSideConnection,
        loop: asyncio.AbstractEventLoop,
        write_via_client: bool = False,
        read_via_client: bool = False,
        **kwargs,
    ):
        self.logger = logging.getLogger(__name__)
        self.session_id = session_id
        self.connection = connection
        self.loop = loop
        self.write_via_client = write_via_client
        self.read_via_client = read_via_client
        self._overlay: dict[str, str] = {}
        self._overlay_originals: dict[str, Optional[str]] = {}
        self.cancelled_event = kwargs.pop("cancelled_event", None)
        self._permission_future: Future[Any] | None = None
        super().__init__(pretty=False, **kwargs)
        self._async_cancelled = asyncio.Event()
        self.logger.info(
            "[paths] ACPIO init session_id=%s root=%s write_via_client=%s read_via_client=%s",
            session_id,
            self.root,
            write_via_client,
            read_via_client,
        )

    def _abs_path(self, filename) -> str:
        return str(safe_abs_path(filename))

    def _run_on_loop(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result()

    def _send_update(self, update: Any, wait: bool = False):
        self.logger.debug("Sending update: %s", update)
        coro = self.connection.session_update(
            session_id=self.session_id, update=update
        )
        try:
            if asyncio.get_running_loop() is self.loop:
                task = self.loop.create_task(coro)
                task.add_done_callback(
                    lambda f: self.logger.debug("Update sent successfully")
                )
                return
        except RuntimeError:
            pass
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        if wait:
            future.result()
        else:
            future.add_done_callback(
                lambda f: self.logger.debug("Update sent successfully")
            )

    def pending_writes(self) -> list[tuple[str, Optional[str], str]]:
        return [
            (path, self._overlay_originals.get(path), content)
            for path, content in self._overlay.items()
        ]

    def clear_overlay(self) -> None:
        self._overlay.clear()
        self._overlay_originals.clear()

    def signal_async_cancel(self) -> None:
        self._async_cancelled.set()

    def reset_async_cancel(self) -> None:
        self._async_cancelled.clear()

    def _is_cancelled(self) -> bool:
        return bool(
            self.cancelled_event is not None and self.cancelled_event.is_set()
        )

    def _read_baseline(self, path: str) -> Optional[str]:
        if self.read_via_client:
            try:
                response = self._run_on_loop(
                    self.connection.read_text_file(
                        path=path, session_id=self.session_id
                    )
                )
                return response.content
            except Exception as e:
                self.logger.warning(
                    "fs/read_text_file failed for %s, falling back to disk: %s",
                    path,
                    e,
                )
        return super().read_text(path, silent=True)

    def read_text(self, filename, silent=False):
        if is_image_file(filename):
            return super().read_text(filename, silent=silent)

        path = self._abs_path(filename)
        if path in self._overlay:
            return self._overlay[path]
        if self.read_via_client:
            try:
                response = self._run_on_loop(
                    self.connection.read_text_file(
                        path=path, session_id=self.session_id
                    )
                )
                return response.content
            except Exception as e:
                self.logger.warning(
                    "fs/read_text_file failed for %s, falling back to disk: %s",
                    path,
                    e,
                )
        return super().read_text(filename, silent=silent)

    def write_text(self, filename, content, max_retries=5, initial_delay=0.1):
        if self.dry_run:
            return
        path = self._abs_path(filename)
        if path not in self._overlay_originals:
            self._overlay_originals[path] = self._read_baseline(path)
        self._overlay[path] = content
        self.logger.info("[overlay] staged write path=%s bytes=%d", path, len(content))

    def flush_pending_writes(self) -> list[str]:
        pending = self.pending_writes()
        if not pending:
            return []

        flushed = []
        if self.write_via_client:
            for path, old_text, new_text in pending:
                self._flush_file_via_client(path, old_text, new_text)
                flushed.append(path)
        else:
            self.logger.warning(
                "Client has no fs.writeTextFile; writing %d overlay file(s) to disk",
                len(pending),
            )
            for path, _old_text, new_text in pending:
                super().write_text(path, new_text)
                flushed.append(path)

        self.clear_overlay()
        return flushed

    def _flush_file_via_client(
        self, path: str, old_text: Optional[str], new_text: str
    ) -> None:
        tool_call_id = str(uuid.uuid4())
        title = f"Edit {path}"
        self._send_update(
            start_tool_call(
                tool_call_id=tool_call_id,
                title=title,
                kind="edit",
                status="pending",
                content=[tool_diff_content(path, new_text, old_text)],
                locations=[ToolCallLocation(path=path)],
            ),
            wait=True,
        )
        try:
            self._run_on_loop(
                self.connection.write_text_file(
                    content=new_text, path=path, session_id=self.session_id
                )
            )
            self._send_update(
                update_tool_call(tool_call_id, status="completed"), wait=True
            )
        except Exception as e:
            self.logger.error("fs/write_text_file failed for %s: %s", path, e)
            self._send_update(update_tool_call(tool_call_id, status="failed"), wait=True)
            self.tool_error(f"ACP write failed, saving to disk: {path}: {e}")
            super().write_text(path, new_text)

    def ai_output(self, content: str):
        # Aider uses this for chat-history only; assistant_output is what Zed should show.
        self.logger.debug("AI history output (%d chars)", len(content or ""))

    def assistant_output(self, message: str, pretty=None):
        self.logger.info("Assistant output received: %s", message)
        if not message:
            return
        visible = strip_aider_edit_blocks(message)
        if not visible:
            if self._overlay:
                return
            visible = message
        chunk = AgentMessageChunk(
            content=TextContentBlock(text=visible, type="text"),
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
        if kwargs.get("log_only"):
            return
        content = " ".join(map(str, messages)).strip()
        if not content:
            return
        if _TOKENS_RE.match(content) or _APPLIED_EDIT_RE.match(content):
            self.logger.info("Suppressed tool output: %s", content)
            return
        self.logger.info("Tool output received: %s", messages)
        if _CHAT_STATUS_RE.match(content):
            self.announce(content)
            return
        chunk = AgentThoughtChunk(
            content=TextContentBlock(text=content, type="text"),
            session_update="agent_thought_chunk",
        )
        self._send_update(chunk, wait=True)

    def send_usage(self, used: int, size: int, cost_usd: float | None = None) -> None:
        used = max(int(used), 0)
        size = max(int(size), used, 1)
        cost = None
        if cost_usd is not None:
            cost = Cost(amount=float(cost_usd), currency="USD")
        self._send_update(
            UsageUpdate(session_update="usage_update", used=used, size=size, cost=cost),
            wait=True,
        )

    def announce(self, message: str) -> None:
        if not message:
            return
        chunk = AgentMessageChunk(
            content=TextContentBlock(text=message, type="text"),
            session_update="agent_message_chunk",
        )
        self._send_update(chunk, wait=True)

    def tool_error(self, message: str = "", strip: bool = True):
        self.logger.error("Tool error: %s", message)
        text = f"Error: {message}".strip()
        if not text:
            return
        chunk = AgentMessageChunk(
            content=TextContentBlock(text=text, type="text"),
            session_update="agent_message_chunk",
        )
        self._send_update(chunk, wait=True)

    def tool_warning(self, message: str = "", strip: bool = True):
        self.logger.warning("Tool warning: %s", message)
        self.tool_output(f"Warning: {message}")

    async def _permission_or_cancel(self, options, tool_call):
        perm = asyncio.create_task(
            self.connection.request_permission(
                options=options,
                session_id=self.session_id,
                tool_call=tool_call,
            )
        )
        cancel_wait = asyncio.create_task(self._async_cancelled.wait())
        done, pending = await asyncio.wait(
            {perm, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if self._async_cancelled.is_set() or perm not in done:
            return None
        return perm.result()

    def confirm_ask(self, question: str, **kwargs: Any) -> bool:
        self.logger.info("Confirmation requested: %s", question)
        if self.yes is True:
            return True
        if self.yes is False:
            return False
        if self._is_cancelled():
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
            self._permission_or_cancel(options, tool_call),
            self.loop,
        )
        self._permission_future = future

        try:
            response = future.result()
            if response is None:
                approved = False
            elif response.outcome.outcome == "cancelled":
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
        except asyncio.CancelledError:
            approved = False
        except Exception as e:
            self.tool_error(f"Permission request failed: {e}")
            approved = False
        finally:
            self._permission_future = None

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
