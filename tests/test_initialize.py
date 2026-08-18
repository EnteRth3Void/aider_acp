import unittest

from acp.schema import (
    ClientCapabilities,
    FileSystemCapabilities,
    SessionAdditionalDirectoriesCapabilities,
    SessionCloseCapabilities,
    SessionListCapabilities,
)

from acp_server.server import AiderAgent


class InitializeCapabilitiesTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_advertises_existing_session_handlers(self):
        agent = AiderAgent()
        client_capabilities = ClientCapabilities(
            fs=FileSystemCapabilities(read_text_file=True, write_text_file=True),
        )
        response = await agent.initialize(
            protocol_version=1,
            client_capabilities=client_capabilities,
        )

        self.assertIs(agent.client_capabilities, client_capabilities)

        caps = response.agent_capabilities
        session_caps = caps.session_capabilities
        self.assertIsInstance(session_caps.list, SessionListCapabilities)
        self.assertIsInstance(session_caps.close, SessionCloseCapabilities)
        self.assertIsInstance(
            session_caps.additional_directories,
            SessionAdditionalDirectoriesCapabilities,
        )
        self.assertFalse(caps.load_session)

        prompt = caps.prompt_capabilities
        self.assertFalse(prompt.image)
        self.assertFalse(prompt.audio)
        self.assertFalse(prompt.embedded_context)

        self.assertFalse(response.auth_methods)


if __name__ == "__main__":
    unittest.main()
