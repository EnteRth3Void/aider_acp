import asyncio
import logging
import sys
from acp.stdio import stdio_streams
from acp.agent.connection import AgentSideConnection
from acp_server.server import AiderAgent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='aider_acp.log',
    filemode='a'
)
logger = logging.getLogger("aider_acp")

async def main():
    logger.info("Starting Aider ACP Server")
    
    # Get stdio streams for ACP communication
    reader, writer = await stdio_streams()
    
    # Initialize the Aider agent
    agent = AiderAgent()
    
    # Create the ACP connection
    # AgentSideConnection(agent_implementation, reader, writer)
    # The agent implementation can be a factory or the instance itself
    async with AgentSideConnection(agent, writer, reader, listening=False) as conn:
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
