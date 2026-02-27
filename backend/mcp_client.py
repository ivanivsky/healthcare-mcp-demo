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


class MCPConnectionError(Exception):
    """Raised when MCP connection fails."""
    pass


class MCPClient:
    """MCP Client that connects to the Health Advisor MCP server."""

    def __init__(self):
        self.config = get_config()
        self.session: Optional[ClientSession] = None
        self.tools: list[dict] = []
        self._read_stream = None
        self._write_stream = None
        self._context_manager = None
        self._connected = False
        self._connect_lock = asyncio.Lock()

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

    @property
    def is_connected(self) -> bool:
        """Check if client appears to be connected."""
        return self._connected and self.session is not None

    async def connect(self):
        """Connect to the MCP server."""
        logger.info(f"MCP_CONNECT attempt url={self.server_url} transport={self.transport}")

        try:
            if self.transport == "sse":
                await self._connect_sse()
            elif self.transport == "stdio":
                await self._connect_stdio()
            else:
                raise ValueError(f"Unsupported transport: {self.transport}")

            # Get available tools
            await self._refresh_tools()
            self._connected = True
            logger.info(f"MCP_CONNECT success url={self.server_url} tools={[t['name'] for t in self.tools]}")
        except Exception as e:
            self._connected = False
            logger.error(f"MCP_CONNECT failure url={self.server_url} error={e}")
            raise MCPConnectionError(f"Failed to connect to MCP server: {e}") from e

    async def ensure_connected(self):
        """
        Ensure the MCP client is connected, reconnecting if needed.

        Raises:
            MCPConnectionError: If connection cannot be established
        """
        # Debug: log entry state
        logger.info(
            f"MCP_ENSURE_CONNECTED enter _connected={self._connected} "
            f"session={self.session is not None} "
            f"read_stream={self._read_stream is not None} "
            f"write_stream={self._write_stream is not None} "
            f"context_manager={self._context_manager is not None}"
        )

        # Fast path: already connected and session alive
        if self.is_connected:
            try:
                await self.ping()
                logger.info("MCP_ENSURE_CONNECTED fast_path ping success")
                return
            except Exception as e:
                logger.warning(f"MCP_DISCONNECT detected during ping: {type(e).__name__}: {e}")
                self._connected = False

        # Acquire lock to prevent concurrent reconnection attempts
        async with self._connect_lock:
            # Re-check after acquiring lock
            if self.is_connected:
                try:
                    await self.ping()
                    logger.info("MCP_ENSURE_CONNECTED lock_recheck ping success")
                    return
                except Exception as e:
                    logger.warning(f"MCP_DISCONNECT in lock recheck: {type(e).__name__}: {e}")
                    self._connected = False

            # Attempt reconnection
            logger.info(f"MCP_RECONNECT attempt url={self.server_url}")
            try:
                # Clean up any stale connection state
                await self._cleanup_connection()
                await self.connect()
                logger.info(
                    f"MCP_RECONNECT success url={self.server_url} "
                    f"session={self.session is not None} "
                    f"streams=({self._read_stream is not None}, {self._write_stream is not None})"
                )
            except Exception as e:
                logger.error(f"MCP_RECONNECT failure url={self.server_url} error_type={type(e).__name__} error={e}")
                raise MCPConnectionError(f"Failed to reconnect to MCP server: {e}") from e

    async def _cleanup_connection(self):
        """Clean up existing connection state without logging disconnect."""
        if self.session:
            try:
                await self.session.__aexit__(None, None, None)
            except Exception:
                pass
            self.session = None

        if self._context_manager:
            try:
                await self._context_manager.__aexit__(None, None, None)
            except Exception:
                pass
            self._context_manager = None

        self._read_stream = None
        self._write_stream = None
        self._connected = False

    async def _connect_sse(self):
        """Connect via SSE transport."""
        logger.info(f"MCP_SSE_CONNECT start url={self.server_url}")

        # Create SSE client context manager
        self._context_manager = sse_client(self.server_url)
        logger.info(f"MCP_SSE_CONNECT sse_client created, entering context...")

        # Enter the context manager to get streams
        streams = await self._context_manager.__aenter__()
        self._read_stream, self._write_stream = streams
        logger.info(
            f"MCP_SSE_CONNECT streams obtained "
            f"read_type={type(self._read_stream).__name__} "
            f"write_type={type(self._write_stream).__name__}"
        )

        # Create and initialize the client session
        self.session = ClientSession(self._read_stream, self._write_stream)
        logger.info("MCP_SSE_CONNECT ClientSession created, entering session...")
        await self.session.__aenter__()
        logger.info("MCP_SSE_CONNECT session entered, initializing...")
        await self.session.initialize()
        logger.info("MCP_SSE_CONNECT session initialized successfully")

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
        logger.info("MCP_DISCONNECT graceful shutdown")
        await self._cleanup_connection()

    async def ping(self) -> None:
        """
        Probe the MCP connection to verify it's alive.

        Raises:
            MCPConnectionError: If not connected or connection is dead
        """
        if not self.session:
            logger.warning("MCP_PING failed: no session")
            raise MCPConnectionError("Not connected to MCP server")

        # Use list_tools() as a lightweight probe - it exercises the streams
        try:
            logger.debug("MCP_PING sending list_tools request...")
            result = await self.session.list_tools()
            logger.debug(f"MCP_PING success tools_count={len(result.tools)}")
        except Exception as e:
            logger.warning(f"MCP_PING failed: {type(e).__name__}: {e}")
            # Mark session as dead
            self._connected = False
            self.session = None
            raise MCPConnectionError(f"MCP connection dead: {e}") from e

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

        Automatically retries once on connection failure after reconnecting.

        Args:
            name: Tool name
            arguments: Tool arguments
            auth: Optional authentication context for tracing

        Returns:
            Tool execution result

        Raises:
            MCPConnectionError: If MCP is not connected and reconnect fails
        """
        # Ensure connected before calling
        await self.ensure_connected()

        # Log with identity context for tracing
        log_context = {
            "tool": name,
            "request_id": auth.request_id if auth else None,
            "sub": auth.sub if auth else None,
        }
        logger.info(f"MCP_TOOL_CALL tool={name} request_id={log_context['request_id']}")

        # Inject auth_context into arguments for server-side authorization
        # Do not overwrite if already present (defense in depth)
        call_arguments = dict(arguments)
        if "auth_context" not in call_arguments and auth is not None:
            call_arguments["auth_context"] = {
                "sub": auth.sub,
                "request_id": auth.request_id,
            }

        # Try the call, with one retry on connection failure
        for attempt in range(2):
            try:
                logger.info(f"MCP_TOOL_CALL_START tool={name} attempt={attempt+1}")
                result = await self.session.call_tool(name, call_arguments)
                logger.info(f"MCP_TOOL_CALL_END tool={name} success=True")
                break
            except Exception as e:
                logger.warning(f"MCP_TOOL_CALL_END tool={name} success=False attempt={attempt+1} error_type={type(e).__name__} error={e}")
                self._connected = False
                self.session = None

                if attempt == 0:
                    # First failure - try to reconnect and retry
                    logger.info(f"MCP_TOOL_CALL retry after reconnect tool={name}")
                    try:
                        await self.ensure_connected()
                    except MCPConnectionError:
                        raise MCPConnectionError(f"MCP tool call failed and reconnect failed: {e}") from e
                else:
                    # Second failure - give up
                    raise MCPConnectionError(f"MCP tool call failed after retry: {e}") from e

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
_keepalive_task: Optional[asyncio.Task] = None


async def get_mcp_client() -> MCPClient:
    """Get or create the MCP client instance."""
    global _client
    if _client is None:
        _client = MCPClient()
        await _client.connect()
    return _client


async def get_mcp_client_if_connected() -> Optional[MCPClient]:
    """Get the MCP client if it exists and is connected, otherwise None."""
    global _client
    if _client is not None and _client.is_connected:
        return _client
    return None


async def ensure_mcp_client_connected() -> MCPClient:
    """
    Get the MCP client, ensuring it's connected.

    Creates client if needed, reconnects if disconnected.

    Raises:
        MCPConnectionError: If connection cannot be established
    """
    global _client
    if _client is None:
        _client = MCPClient()

    await _client.ensure_connected()
    return _client


async def shutdown_mcp_client():
    """Shutdown the MCP client and stop keepalive."""
    global _client, _keepalive_task

    # Stop keepalive task
    if _keepalive_task is not None:
        _keepalive_task.cancel()
        try:
            await _keepalive_task
        except asyncio.CancelledError:
            pass
        _keepalive_task = None

    # Disconnect client
    if _client:
        await _client.disconnect()
        _client = None


def reset_mcp_client():
    """
    Reset the MCP client without graceful disconnect.
    Used when connection is already broken and we need to force a fresh connection.
    """
    global _client
    if _client:
        _client._connected = False
        _client.session = None
    _client = None
    logger.info("MCP client reset (will reconnect on next request)")


async def _keepalive_loop(interval_seconds: int = 25):
    """
    Background task that pings MCP server periodically to keep connection alive.

    Args:
        interval_seconds: Seconds between pings (default 25s)
    """
    global _client
    logger.info(f"MCP_KEEPALIVE started interval={interval_seconds}s")
    ping_count = 0

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            ping_count += 1

            if _client is not None:
                is_conn = _client.is_connected
                has_session = _client.session is not None
                logger.info(
                    f"MCP_KEEPALIVE ping_attempt={ping_count} "
                    f"is_connected={is_conn} has_session={has_session}"
                )

                if is_conn:
                    try:
                        await _client.ping()
                        logger.info(f"MCP_KEEPALIVE ping_success count={ping_count}")
                    except Exception as e:
                        logger.warning(f"MCP_KEEPALIVE ping_failed count={ping_count}: {type(e).__name__}: {e}")
                        # Mark as disconnected; next request will reconnect
                        _client._connected = False
                else:
                    logger.info(f"MCP_KEEPALIVE skipped (not connected) count={ping_count}")
            else:
                logger.info(f"MCP_KEEPALIVE skipped (no client) count={ping_count}")

        except asyncio.CancelledError:
            logger.info("MCP_KEEPALIVE stopped")
            break
        except Exception as e:
            logger.error(f"MCP_KEEPALIVE error: {type(e).__name__}: {e}")


def start_keepalive(interval_seconds: int = 25):
    """
    Start the MCP keepalive background task.

    Args:
        interval_seconds: Seconds between pings (default 25s)
    """
    global _keepalive_task
    if _keepalive_task is None or _keepalive_task.done():
        _keepalive_task = asyncio.create_task(_keepalive_loop(interval_seconds))
        logger.info(f"MCP_KEEPALIVE task created interval={interval_seconds}s")
