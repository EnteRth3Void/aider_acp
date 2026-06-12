import logging
import uuid
from typing import Any, Dict, List, Optional

from acp.interfaces import Agent, Client
from acp.schema import (
    AgentCapabilities,
    AudioContentBlock,
    ClientCapabilities,
    CloseSessionRequest,
    CloseSessionResponse,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    Implementation,
    InitializeRequest,
    InitializeResponse,
    ListSessionsRequest,
    ListSessionsResponse,
    NewSessionRequest,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
    ResourceContentBlock,
    SessionInfo,
    TextContentBlock,
)

from .session import AiderSession

# Logger erstellen
logger = logging.getLogger("AiderAgent")
logger.setLevel(logging.INFO)

# Console Handler hinzufügen
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(console_handler)


class AiderAgent(Agent):
    def __init__(self):
        self.sessions: Dict[str, AiderSession] = {}
        self.client: Optional[Client] = None

    def on_connect(self, conn: Client) -> None:
        self.client = conn
        logger.info("Client connected")

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Optional[ClientCapabilities] = None,
        client_info: Optional[Implementation] = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        logger.info("Initializing with protocol version %s", protocol_version)
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_info=Implementation(
                name="aider-acp", version="0.1.0", title="Aider ACP Server"
            ),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: Optional[List[str]] = None,
        mcp_servers: Optional[List[Any]] = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        session_id = str(uuid.uuid4())
        logger.info("Creating new session with ID %s", session_id)
        import asyncio

        loop = asyncio.get_running_loop()

        session = AiderSession(
            session_id=session_id, connection=self.client, loop=loop, cwd=cwd
        )
        self.sessions[session_id] = session

        # Optionally pre-initialize coder
        # await session.initialize_coder()

        return NewSessionResponse(session_id=session_id)

    async def prompt(
        self,
        prompt: List[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
        session_id: str,
        message_id: Optional[str] = None,
        **kwargs: Any,
    ) -> PromptResponse:
        logger.info("Prompt received for session ID %s", session_id)
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Extract text from prompt blocks
        text_parts = []
        for block in prompt:
            if isinstance(block, TextContentBlock):
                text_parts.append(block.text)

        prompt_text = "\n".join(text_parts)

        # Run Aider in background thread
        import asyncio

        asyncio.create_task(session.run_prompt(prompt_text))

        return PromptResponse(
            message_id=message_id or str(uuid.uuid4()), stop_reason="end_turn"
        )

    async def list_sessions(
        self,
        additional_directories: Optional[List[str]] = None,
        cursor: Optional[str] = None,
        cwd: Optional[str] = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        sessions_info = [
            SessionInfo(session_id=s.session_id, cwd=s.cwd, title="Aider Session")
            for s in self.sessions.values()
        ]
        return ListSessionsResponse(sessions=sessions_info)

    async def close_session(
        self, session_id: str, **kwargs: Any
    ) -> CloseSessionResponse:
        session = self.sessions.pop(session_id, None)
        if session:
            session.close()
        return CloseSessionResponse()

    # Optional extension methods
    async def ext_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: Dict[str, Any]) -> None:
        pass
