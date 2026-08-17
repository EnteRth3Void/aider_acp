import asyncio
import logging
import sys
from pathlib import Path

from acp.stdio import stdio_streams
from acp.agent.connection import AgentSideConnection
from acp_server.server import AiderAgent

# Keep the log next to the adapter, not in the user's project cwd.
_LOG_PATH = Path(__file__).resolve().parent / "aider_acp.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(_LOG_PATH, mode="a"),
        logging.StreamHandler(sys.stderr),
    ],
    force=True,
)
logger = logging.getLogger(__name__)

async def main():
    import os

    logger.info("Starting Aider ACP Server with enhanced logging")
    logger.info("[paths] server process cwd=%s", os.getcwd())
    
    # Get stdio streams for ACP communication
    logger.debug("Getting stdio streams for ACP communication")
    reader, writer = await stdio_streams()
    
    # Initialize the Aider agent
    logger.debug("Initializing the Aider agent")
    agent = AiderAgent()
    
    # Create the ACP connection
    # AgentSideConnection(agent_implementation, reader, writer)
    # The agent implementation can be a factory or the instance itself
    async with AgentSideConnection(
        agent, 
        writer, 
        reader, 
        listening=False, 
        use_unstable_protocol=True
    ) as conn:
        logger.info("ACP Connection established")
        # Start listening for messages
        await conn.listen()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.exception("Unexpected error occurred")
        sys.exit(1)
