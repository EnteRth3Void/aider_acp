import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from acp.interfaces import Agent, Client
from acp.schema import (
    AgentCapabilities,
    AudioContentBlock,
    ClientCapabilities,
    CloseSessionResponse,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    NewSessionResponse,
    PromptResponse,
    ResourceContentBlock,
    SessionAdditionalDirectoriesCapabilities,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionInfo,
    SessionListCapabilities,
    SetSessionModeResponse,
    SetSessionModelResponse,
    TextContentBlock,
)

from .model_catalog import (
    build_session_model_state,
    catalog_error_message,
    filter_available_models,
    parse_catalog_env,
)
from .session_modes import VALID_MODE_IDS, build_session_mode_state
from .session import AiderSession

COMMANDS_ADVERTISE_DELAY = 0.3


class AiderAgent(Agent):
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.sessions: Dict[str, AiderSession] = {}
        self.client: Optional[Client] = None
        self.client_capabilities: Optional[ClientCapabilities] = None

    def on_connect(self, conn: Client) -> None:
        self.client = conn
        self.logger.info("Client connected")

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Optional[ClientCapabilities] = None,
        client_info: Optional[Implementation] = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        self.logger.info("Initializing with protocol version %s", protocol_version)
        self.client_capabilities = client_capabilities
        fs = getattr(client_capabilities, "fs", None) if client_capabilities else None
        self.logger.info(
            "Client fs capabilities writeTextFile=%s readTextFile=%s",
            getattr(fs, "write_text_file", False) if fs else False,
            getattr(fs, "read_text_file", False) if fs else False,
        )
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_info=Implementation(
                name="aider-acp", version="0.1.0", title="Aider ACP Server"
            ),
            agent_capabilities=AgentCapabilities(
                load_session=False,
                session_capabilities=SessionCapabilities(
                    list=SessionListCapabilities(),
                    close=SessionCloseCapabilities(),
                    additional_directories=SessionAdditionalDirectoriesCapabilities(),
                ),
            ),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: Optional[List[str]] = None,
        mcp_servers: Optional[List[Any]] = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        self.logger.debug(
            "[paths] session/new cwd=%r additional_directories=%r",
            cwd,
            additional_directories,
        )
        session_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()

        configured_models = parse_catalog_env()
        available_models = filter_available_models(configured_models)
        if not available_models:
            raise ValueError(catalog_error_message(configured_models))

        current_model_id = available_models[0]

        session = AiderSession(
            session_id=session_id,
            connection=self.client,
            loop=loop,
            cwd=cwd,
            additional_directories=additional_directories,
            mcp_servers=mcp_servers,
            client_capabilities=self.client_capabilities,
            available_model_ids=available_models,
            current_model_id=current_model_id,
        )
        self.sessions[session_id] = session
        self.logger.info("Created session %s", session_id)
        self.logger.debug(
            "[paths] session/new created session_id=%s session.cwd=%s io.root=%s models=%s",
            session_id,
            session.cwd,
            session.io.root,
            available_models,
        )

        loop.create_task(self._advertise_commands(session_id))

        return NewSessionResponse(
            session_id=session_id,
            models=build_session_model_state(available_models, current_model_id),
            modes=build_session_mode_state("code"),
        )

    async def _advertise_commands(self, session_id: str) -> None:
        await asyncio.sleep(COMMANDS_ADVERTISE_DELAY)
        session = self.sessions.get(session_id)
        if session is None:
            return
        await session.advertise_commands()

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
        self.logger.info("Prompt received for session ID %s", session_id)
        try:
            session = self.sessions[session_id]
        except KeyError:
            self.logger.error("Session %s not found", session_id)
            raise ValueError(f"Session {session_id} not found")

        coder_root = session.coder.root if session.coder else None
        self.logger.debug(
            "[paths] session/prompt session_id=%s session.cwd=%s io.root=%s coder.root=%s getcwd=%s",
            session_id,
            session.cwd,
            session.io.root,
            coder_root,
            os.getcwd(),
        )

        text_parts = []
        resource_names = []
        for block in prompt:
            if isinstance(block, TextContentBlock):
                text_parts.append(block.text)
            elif isinstance(block, ResourceContentBlock):
                if block.name:
                    resource_names.append(block.name)
            elif isinstance(block, EmbeddedResourceContentBlock):
                name = getattr(block.resource, "name", None) or Path(
                    unquote(urlparse(block.resource.uri).path)
                ).name
                if name:
                    resource_names.append(name)

        prompt_text = "\n".join(text_parts)

        if session.prompt_running:
            raise ValueError("Prompt already in progress for this session")

        stop_reason = await session.run_prompt(
            prompt_text, resource_names=resource_names
        )

        response_kwargs: dict[str, Any] = {"stop_reason": stop_reason}
        if message_id is not None:
            response_kwargs["user_message_id"] = message_id
        return PromptResponse(**response_kwargs)

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            self.logger.warning("Cancel: session %s not found", session_id)
            return
        session.cancel()

    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModelResponse:
        session = self.sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        if session.prompt_running:
            raise ValueError("Cannot change model while a prompt is running")
        if model_id not in session.available_model_ids:
            raise ValueError(f"Unknown model: {model_id}")

        await session.set_model(model_id)
        return SetSessionModelResponse()

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModeResponse:
        session = self.sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        if session.prompt_running:
            raise ValueError("Cannot change mode while a prompt is running")
        if mode_id not in VALID_MODE_IDS:
            raise ValueError(f"Unknown mode: {mode_id}")

        await session.set_mode(mode_id)
        return SetSessionModeResponse()

    async def list_sessions(
        self,
        additional_directories: Optional[List[str]] = None,
        cursor: Optional[str] = None,
        cwd: Optional[str] = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        self.logger.info("Listing sessions")
        sessions_info = [
            SessionInfo(session_id=s.session_id, cwd=s.cwd, title="Aider Session")
            for s in self.sessions.values()
        ]
        return ListSessionsResponse(sessions=sessions_info)

    async def close_session(
        self, session_id: str, **kwargs: Any
    ) -> CloseSessionResponse:
        self.logger.info("Closing session ID %s", session_id)
        session = self.sessions.pop(session_id, None)
        if session:
            session.close()
        return CloseSessionResponse()

    # Optional extension methods
    async def ext_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: Dict[str, Any]) -> None:
        pass
