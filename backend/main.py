"""
My Health Access Backend API

FastAPI application that serves as the MCP client and Claude agent orchestrator.
"""

import asyncio
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import get_config
from backend.mcp_client import get_mcp_client, shutdown_mcp_client, reset_mcp_client
from backend.claude_agent import HealthAdvisorAgent
from backend.debug_logger import get_debug_logger
from backend.auth import get_auth_context, VALID_USERS

# Load config
config = get_config()

# Setup logging
log_level = config.get("logging", {}).get("level", "INFO")
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("backend")

# File logging if enabled
if config.get("logging", {}).get("log_to_file", False):
    log_file = config.get("logging", {}).get("log_file_path", "logs/health_advisor.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logging.getLogger().addHandler(file_handler)


# Global state
mcp_client = None
agent = None
mcp_reconnect_lock = asyncio.Lock()


# ============================================================================
# MCP Connection Management
# ============================================================================

def is_connection_error(exc: Exception) -> bool:
    """Check if an exception indicates a connection failure."""
    error_indicators = (
        "connection",
        "connect",
        "closed",
        "eof",
        "disconnected",
        "broken pipe",
        "reset by peer",
        "timed out",
        "refused",
    )
    error_str = str(exc).lower()
    error_type = type(exc).__name__.lower()
    return any(ind in error_str or ind in error_type for ind in error_indicators)


async def ensure_mcp_connected():
    """
    Ensure MCP client is connected, attempting reconnect if needed.

    Uses a lock to prevent thundering herd on reconnect.
    Pings the connection on fast path to detect dead SSE sessions.

    Returns:
        Tuple of (mcp_client, agent)

    Raises:
        HTTPException(503) if connection cannot be established
    """
    global mcp_client, agent

    # Fast path: already connected - verify with ping
    if mcp_client is not None and agent is not None:
        try:
            await mcp_client.ping()
            return mcp_client, agent
        except Exception as e:
            logger.warning(f"MCP ping failed on fast path: {e}")
            invalidate_mcp_connection()

    # Acquire lock to prevent thundering herd
    async with mcp_reconnect_lock:
        # Re-check inside lock - another request may have reconnected
        if mcp_client is not None and agent is not None:
            try:
                await mcp_client.ping()
                return mcp_client, agent
            except Exception as e:
                logger.warning(f"MCP ping failed inside lock: {e}")
                invalidate_mcp_connection()

        debug_logger = get_debug_logger()

        # Attempt connection with one retry for transient startup timing
        max_attempts = 2
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"MCP reconnect attempt {attempt}/{max_attempts}...")
                mcp_client = await get_mcp_client()
                agent = HealthAdvisorAgent(mcp_client, debug_logger)
                logger.info("MCP connection established successfully")
                return mcp_client, agent
            except Exception as e:
                last_error = e
                logger.warning(f"MCP connection attempt {attempt} failed: {e}")
                # Reset global state on failure
                mcp_client = None
                agent = None
                reset_mcp_client()
                if attempt < max_attempts:
                    await asyncio.sleep(0.3)  # 300ms delay before retry

        # All attempts failed
        logger.error(f"Failed to connect to MCP server after {max_attempts} attempts: {last_error}")
        raise HTTPException(
            status_code=503,
            detail="MCP server not connected. Please ensure the MCP server is running."
        )


def invalidate_mcp_connection():
    """Invalidate the MCP connection so next request triggers reconnect."""
    global mcp_client, agent
    logger.warning("Invalidating MCP connection due to error")
    mcp_client = None
    agent = None
    # Also reset the cached client in mcp_client module
    reset_mcp_client()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    global mcp_client, agent

    logger.info("Starting My Health Access backend...")

    # Initialize debug logger
    debug_logger = get_debug_logger()

    # Connect to MCP server
    try:
        mcp_client = await get_mcp_client()
        agent = HealthAdvisorAgent(mcp_client, debug_logger)
        logger.info("Connected to MCP server")
    except Exception as e:
        logger.warning(f"Could not connect to MCP server: {e}")
        logger.warning("Chat functionality will be unavailable until MCP server is running")

    yield

    # Shutdown
    logger.info("Shutting down My Health Access backend...")
    await shutdown_mcp_client()


# Create FastAPI app
app = FastAPI(
    title="My Health Access API",
    description="Backend API for My Health Access healthcare demo",
    version="1.0.0",
    lifespan=lifespan,
)

# Session middleware - use SESSION_SECRET from env or generate safe fallback
session_secret = os.environ.get("SESSION_SECRET")
if not session_secret:
    # Generate a persistent secret for local dev (logged warning)
    session_secret = secrets.token_hex(32)
    logger.warning("SESSION_SECRET not set; using generated secret (sessions won't persist across restarts)")

# Use HTTPS-only cookies when in production (Cloud Run is always HTTPS)
https_only = os.environ.get("HTTPS_ONLY", "false").lower() == "true"

app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="mha_session",
    same_site="lax",
    https_only=https_only,
)

# CORS middleware
if config.get("backend", {}).get("cors_enabled", True):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, specify exact origins
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ============================================================================
# Pydantic Models
# ============================================================================

class ChatRequest(BaseModel):
    message: str
    patient_id: int
    patient_name: str
    conversation_history: Optional[list[dict]] = None


class ChatResponse(BaseModel):
    response: str
    tool_calls: list[dict]
    usage: dict


class PatientInfo(BaseModel):
    id: int
    first_name: str
    last_name: str
    member_id: str


class LoginRequest(BaseModel):
    username: str
    password: str


# ============================================================================
# Auth Endpoints
# ============================================================================

@app.post("/api/auth/login")
async def login(request: Request, login_request: LoginRequest):
    """Authenticate user and create session."""
    username = login_request.username.strip().lower()
    password = login_request.password.strip()

    # Validate username
    if username not in VALID_USERS:
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_credentials", "message": "Invalid username or password"}
        )

    # Validate password is non-empty
    if not password:
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_credentials", "message": "Password is required"}
        )

    # Set session
    request.session["sub"] = username
    logger.info(f"User '{username}' logged in")

    return {"sub": username}


@app.post("/api/auth/logout")
async def logout(request: Request):
    """Clear session and log out."""
    sub = request.session.get("sub")
    request.session.clear()
    if sub:
        logger.info(f"User '{sub}' logged out")
    return {"ok": True}


@app.get("/api/auth/me")
async def get_current_user(request: Request):
    """Get current authenticated user info."""
    sub = request.session.get("sub")

    if not sub:
        return {"authenticated": False}

    # Get allowed patient IDs from config
    authz_config = config.get("authz", {})
    user_patient_map = authz_config.get("user_patient_map", {})
    allowed_patient_id = user_patient_map.get(sub)

    # Return as list for consistency
    allowed_patient_ids = [allowed_patient_id] if allowed_patient_id else []

    return {
        "authenticated": True,
        "sub": sub,
        "allowed_patient_ids": allowed_patient_ids,
    }


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "mcp_connected": mcp_client is not None,
        "agent_ready": agent is not None,
    }


@app.get("/api/patients", response_model=list[PatientInfo])
async def list_patients(request: Request):
    """Get list of patients for selector dropdown."""
    # Ensure MCP connection (auto-reconnect if needed)
    client, _ = await ensure_mcp_connected()

    # Extract auth context from request
    auth = get_auth_context(request)

    try:
        result = await client.call_tool("list_patients", {}, auth=auth)
        # Parse the result
        content = result.get("content", [])
        if content and content[0].get("type") == "text":
            import json
            data = json.loads(content[0]["text"])
            return data.get("patients", [])
        return []
    except Exception as e:
        # Check if this is a connection error - invalidate client for next request
        if is_connection_error(e):
            invalidate_mcp_connection()
            raise HTTPException(status_code=503, detail="MCP connection lost. Please retry.")
        logger.error(f"Error listing patients: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list patients: {type(e).__name__}: {str(e)}")


@app.get("/api/patients/{patient_id}")
async def get_patient(request: Request, patient_id: int):
    """Get patient details."""
    # Ensure MCP connection (auto-reconnect if needed)
    client, _ = await ensure_mcp_connected()

    # Extract auth context from request
    auth = get_auth_context(request)

    try:
        result = await client.call_tool("get_patient_demographics", {"patient_id": patient_id}, auth=auth)
        content = result.get("content", [])
        if content and content[0].get("type") == "text":
            import json
            return json.loads(content[0]["text"])
        raise HTTPException(status_code=404, detail="Patient not found")
    except HTTPException:
        raise
    except Exception as e:
        # Check if this is a connection error - invalidate client for next request
        if is_connection_error(e):
            invalidate_mcp_connection()
            raise HTTPException(status_code=503, detail="MCP connection lost. Please retry.")
        logger.error(f"Error getting patient: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get patient: {type(e).__name__}: {str(e)}")


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: Request, chat_request: ChatRequest):
    """Process a chat message through the Claude agent."""
    # Ensure MCP connection (auto-reconnect if needed)
    _, chat_agent = await ensure_mcp_connected()

    # Extract auth context from request
    auth = get_auth_context(request)

    debug_logger = get_debug_logger()
    debug_logger.log_agent_reasoning({
        "action": "chat_request",
        "patient_id": chat_request.patient_id,
        "message_preview": chat_request.message[:100],
        "request_id": auth.request_id,
        "sub": auth.sub,
    })

    try:
        result = await chat_agent.chat(
            message=chat_request.message,
            patient_id=chat_request.patient_id,
            patient_name=chat_request.patient_name,
            conversation_history=chat_request.conversation_history or [],
            auth=auth,
        )
        return ChatResponse(**result)
    except Exception as e:
        # Check if this is a connection error - invalidate client for next request
        if is_connection_error(e):
            invalidate_mcp_connection()
            raise HTTPException(status_code=503, detail="MCP connection lost. Please retry.")
        logger.error(f"Chat error: {e}")
        debug_logger.log_error({"error": str(e), "type": "chat_error", "request_id": auth.request_id})
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Debug Endpoints
# ============================================================================

@app.get("/api/debug/events")
async def get_debug_events(
    event_type: Optional[str] = None,
    since_id: Optional[int] = None,
    limit: int = 100,
):
    """Get debug events for the debug panel."""
    debug_logger = get_debug_logger()
    events = debug_logger.get_events(
        event_type=event_type,
        since_id=since_id,
        limit=limit,
    )
    return {"events": events}


@app.delete("/api/debug/events")
async def clear_debug_events():
    """Clear all debug events."""
    debug_logger = get_debug_logger()
    debug_logger.clear_events()
    return {"status": "cleared"}


@app.get("/api/debug/config")
async def get_debug_config():
    """Get current configuration (for debug panel display)."""
    # Return safe config info (no secrets)
    return {
        "app": config.get("app", {}),
        "mcp": config.get("mcp", {}),
        "backend": {
            "host": config.get("backend", {}).get("host"),
            "port": config.get("backend", {}).get("port"),
        },
        "security": config.get("security", {}),
        "logging": config.get("logging", {}),
    }


# ============================================================================
# Static Files (Frontend)
# ============================================================================

# Mount frontend static files
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")


@app.get("/")
async def serve_frontend():
    """Serve the main frontend page."""
    index_path = frontend_path / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse(
        {"error": "Frontend not found. Place index.html in frontend/"},
        status_code=404,
    )


@app.get("/debug")
async def serve_debug():
    """Serve the debug panel page."""
    if not config.get("logging", {}).get("debug_panel_enabled", True):
        return JSONResponse(
            {"error": "Debug panel is disabled in configuration"},
            status_code=403,
        )

    debug_path = frontend_path / "debug.html"
    if debug_path.exists():
        return FileResponse(debug_path)
    return JSONResponse(
        {"error": "Debug page not found. Place debug.html in frontend/"},
        status_code=404,
    )


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    # Support environment variable overrides for Cloud Run
    host = os.environ.get("BACKEND_HOST", config.get("backend", {}).get("host", "localhost"))
    port = int(os.environ.get("BACKEND_PORT", config.get("backend", {}).get("port", 8080)))

    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=False,
    )
