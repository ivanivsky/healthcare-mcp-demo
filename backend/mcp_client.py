"""
MCP Client for Health Advisor.
Connects to the MCP server and provides tool access.
"""

import asyncio
import logging
from typing import Any, Optional
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client, StdioServerParameters

from backend.config import get_config
from backend.auth_context import AuthContext

logger = logging.getLogger("mcp_client")


class MCPClient:
    """MCP Client that connects to the Health Advisor MCP server."""

    def __init__(self):
        self.config = get_config()
        self.session: Optional[ClientSession] = None
        self.tools: list[dict] = []
        self._read_stream = None
        self._write_stream = None
        self._context_manager = None

    @property
    def mcp_config(self) -> dict:
        return self.config.get("mcp", {})

    @property
    def transport(self) -> str:
        return self.mcp_config.get("transport", "sse")

    @property
    def host(self) -> str:
        return self.mcp_config.get("host", "localhost")

    @property
    def port(self) -> int:
        return self.mcp_config.get("port", 8001)

    @property
    def server_url(self) -> str:
        # Use MCP_SERVER_URL if provided (for Cloud Run), otherwise construct from host/port
        if self.mcp_config.get("server_url"):
            return self.mcp_config["server_url"]
        return f"http://{self.host}:{self.port}/sse"

    async def connect(self):
        """Connect to the MCP server."""
        logger.info(f"Connecting to MCP server via {self.transport}...")

        if self.transport == "sse":
            await self._connect_sse()
        elif self.transport == "stdio":
            await self._connect_stdio()
        else:
            raise ValueError(f"Unsupported transport: {self.transport}")

        # Get available tools
        await self._refresh_tools()
        logger.info(f"Connected. Available tools: {[t['name'] for t in self.tools]}")

    async def _connect_sse(self):
        """Connect via SSE transport."""
        logger.info(f"Connecting to SSE endpoint: {self.server_url}")
        self._context_manager = sse_client(self.server_url)
        streams = await self._context_manager.__aenter__()
        self._read_stream, self._write_stream = streams

        self.session = ClientSession(self._read_stream, self._write_stream)
        await self.session.__aenter__()
        await self.session.initialize()

    async def _connect_stdio(self):
        """Connect via stdio transport."""
        import sys
        from pathlib import Path

        server_script = str(Path(__file__).parent.parent / "mcp_server" / "server.py")
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[server_script],
        )

        self._context_manager = stdio_client(server_params)
        streams = await self._context_manager.__aenter__()
        self._read_stream, self._write_stream = streams

        self.session = ClientSession(self._read_stream, self._write_stream)
        await self.session.__aenter__()
        await self.session.initialize()

    async def disconnect(self):
        """Disconnect from the MCP server."""
        if self.session:
            await self.session.__aexit__(None, None, None)
            self.session = None

        if self._context_manager:
            await self._context_manager.__aexit__(None, None, None)
            self._context_manager = None

        # Clear streams to ensure clean state
        self._read_stream = None
        self._write_stream = None

        logger.info("Disconnected from MCP server")

    async def ping(self) -> None:
        """
        Probe the MCP connection to verify it's alive.

        Raises:
            RuntimeError: If not connected or connection is dead
        """
        if not self.session:
            raise RuntimeError("Not connected to MCP server")

        # Use list_tools() as a lightweight probe - it exercises the streams
        try:
            await self.session.list_tools()
        except Exception as e:
            logger.warning(f"MCP ping failed: {e}")
            # Mark session as dead
            self.session = None
            raise RuntimeError(f"MCP connection dead: {e}") from e

    async def _refresh_tools(self):
        """Refresh the list of available tools."""
        if not self.session:
            raise RuntimeError("Not connected to MCP server")

        result = await self.session.list_tools()
        self.tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema,
            }
            for tool in result.tools
        ]

    def get_tools_for_claude(self) -> list[dict]:
        """Get tools formatted for Claude API."""
        return [
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["input_schema"],
            }
            for tool in self.tools
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], auth: AuthContext | None = None
    ) -> dict:
        """
        Call an MCP tool and return the result.

        Args:
            name: Tool name
            arguments: Tool arguments
            auth: Optional authentication context for tracing

        Returns:
            Tool execution result
        """
        if not self.session:
            raise RuntimeError("Not connected to MCP server")

        # Log with identity context for tracing
        log_context = {
            "tool": name,
            "request_id": auth.request_id if auth else None,
            "sub": auth.sub if auth else None,
        }
        logger.info(f"Calling MCP tool: {log_context}")

        # Inject auth_context into arguments for server-side authorization
        # Do not overwrite if already present (defense in depth)
        call_arguments = dict(arguments)
        if "auth_context" not in call_arguments and auth is not None:
            call_arguments["auth_context"] = {
                "sub": auth.sub,
                "request_id": auth.request_id,
            }

        # Wrap call in try/except to fail closed on transport errors
        try:
            result = await self.session.call_tool(name, call_arguments)
        except Exception as e:
            logger.warning(f"MCP call_tool failed, marking session dead: {e}")
            self.session = None
            raise

        # Extract content from result
        content = []
        for item in result.content:
            if hasattr(item, "text"):
                content.append({"type": "text", "text": item.text})
            elif hasattr(item, "data"):
                content.append({"type": "data", "data": item.data})

        response = {
            "tool_name": name,
            "arguments": arguments,
            "content": content,
            "is_error": result.isError if hasattr(result, "isError") else False,
        }

        logger.debug(f"Tool result: {response}")
        return response


# Global client instance
_client: Optional[MCPClient] = None


async def get_mcp_client() -> MCPClient:
    """Get or create the MCP client instance."""
    global _client
    if _client is None:
        _client = MCPClient()
        await _client.connect()
    return _client


async def shutdown_mcp_client():
    """Shutdown the MCP client."""
    global _client
    if _client:
        await _client.disconnect()
        _client = None


def reset_mcp_client():
    """
    Reset the MCP client without graceful disconnect.
    Used when connection is already broken and we need to force a fresh connection.
    """
    global _client
    _client = None
    logger.info("MCP client reset (will reconnect on next request)")
