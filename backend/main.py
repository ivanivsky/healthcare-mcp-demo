"""
My Health Access Backend API

FastAPI application that serves as the MCP client and Claude agent orchestrator.
Uses Firebase Authentication exclusively - no session/cookie auth.
"""

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import get_config
from backend.mcp_client import (
    get_mcp_client,
    shutdown_mcp_client,
    reset_mcp_client,
    ensure_mcp_client_connected,
    start_keepalive,
    MCPConnectionError,
)
from backend.claude_agent import HealthAdvisorAgent
from backend.debug_logger import get_debug_logger
from backend.firebase_auth import (
    init_firebase,
    require_firebase_auth,
    log_auth_decision,
    FirebaseUser,
)
from backend.patient_access import (
    init_patient_access_db,
    seed_patient_access,
    get_allowed_patient_ids,
    check_patient_access,
)
from backend.patient_db import (
    get_patients_by_ids,
    get_patient_by_id as get_patient_from_db,
)

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

    Uses the MCPClient's built-in ensure_connected() with auto-reconnect.

    Returns:
        Tuple of (mcp_client, agent)

    Raises:
        HTTPException(503) if connection cannot be established
    """
    global mcp_client, agent

    try:
        # Use the MCP client's ensure_connected which handles reconnection
        mcp_client = await ensure_mcp_client_connected()

        # Create agent if needed
        if agent is None:
            debug_logger = get_debug_logger()
            agent = HealthAdvisorAgent(mcp_client, debug_logger)

        return mcp_client, agent

    except MCPConnectionError as e:
        logger.error(f"MCP connection failed: {e}")
        # Reset state on failure
        mcp_client = None
        agent = None
        raise HTTPException(
            status_code=503,
            detail={
                "error": "MCP_UNAVAILABLE",
                "message": "MCP server not connected",
                "reason": "mcp_connection_failed",
            }
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

    # Log SDK versions for debugging compatibility issues
    try:
        import anthropic
        import httpx
        logger.info(f"SDK_VERSIONS anthropic={anthropic.__version__} httpx={httpx.__version__}")
    except Exception as e:
        logger.warning(f"Could not log SDK versions: {e}")

    # Initialize Firebase Admin SDK
    if init_firebase():
        logger.info("Firebase authentication enabled")
    else:
        logger.warning("Firebase authentication not available (missing credentials)")

    # Initialize patient access database
    await init_patient_access_db()
    await seed_patient_access()
    logger.info("Patient access database initialized")

    # Initialize debug logger
    debug_logger = get_debug_logger()

    # Connect to MCP server
    try:
        mcp_client = await get_mcp_client()
        agent = HealthAdvisorAgent(mcp_client, debug_logger)
        logger.info("Connected to MCP server")
        # Start keepalive to maintain SSE connection
        start_keepalive(interval_seconds=25)
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
    description="Backend API for My Health Access healthcare demo (Firebase Auth only)",
    version="2.0.0",
    lifespan=lifespan,
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


# ============================================================================
# Authorization Helpers
# ============================================================================

async def require_patient_access(user: FirebaseUser, patient_id: int) -> None:
    """
    Check if user has access to the specified patient.

    Args:
        user: Authenticated Firebase user
        patient_id: Patient ID to check access for

    Raises:
        HTTPException 403 if access denied
    """
    has_access = await check_patient_access(user.uid, patient_id)
    if not has_access:
        logger.warning(f"AUTHZ_DENIED uid={user.uid} patient_id={patient_id}")
        raise HTTPException(
            status_code=403,
            detail={
                "error": "FORBIDDEN",
                "message": "Not authorized for requested patient.",
                "patient_id": str(patient_id),
            }
        )
    logger.info(f"AUTHZ_ALLOWED uid={user.uid} patient_id={patient_id}")


# ============================================================================
# Auth Endpoints (Firebase only)
# ============================================================================

@app.get("/api/whoami")
async def whoami(request: Request, user: FirebaseUser = Depends(require_firebase_auth)):
    """
    Get current Firebase-authenticated user identity.

    Requires valid Firebase ID token in Authorization header.
    Returns deterministic JSON with user identity.
    """
    # Get allowed patient IDs for this user
    allowed_patients = await get_allowed_patient_ids(user.uid)

    # Log successful auth decision
    log_auth_decision(
        path=request.url.path,
        uid=user.uid,
        decision="allow",
    )

    return {
        "uid": user.uid,
        "email": user.email,
        "email_verified": user.email_verified,
        "allowed_patient_ids": allowed_patients,
        "claims": user.claims,
    }


# ============================================================================
# API Endpoints (Protected with Firebase Auth)
# ============================================================================

@app.get("/api/health")
async def health_check():
    """Health check endpoint (public)."""
    mcp_connected = mcp_client is not None and mcp_client.is_connected
    return {
        "status": "healthy",
        "mcp_connected": mcp_connected,
        "agent_ready": agent is not None,
    }


@app.get("/api/patients", response_model=list[PatientInfo])
async def list_patients(user: FirebaseUser = Depends(require_firebase_auth)):
    """
    Get list of patients the authenticated user can access.

    Requires Firebase authentication. Returns only patients the user is authorized for.
    This endpoint does NOT require MCP - it reads directly from the patient database.
    """
    # Get allowed patient IDs for this user
    allowed_patient_ids = await get_allowed_patient_ids(user.uid)

    logger.info(f"PATIENT_LIST uid={user.uid} allowed_ids={allowed_patient_ids}")

    if not allowed_patient_ids:
        logger.info(f"PATIENT_LIST no_patient_access uid={user.uid}")
        return []

    try:
        # Query patient database directly (no MCP dependency)
        patients = await get_patients_by_ids(allowed_patient_ids)
        logger.info(f"PATIENT_LIST returned_count={len(patients)}")
        return patients
    except Exception as e:
        logger.error(f"PATIENT_LIST error uid={user.uid}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load patients.")


@app.get("/api/patients/{patient_id}")
async def get_patient(patient_id: int, user: FirebaseUser = Depends(require_firebase_auth)):
    """
    Get patient details.

    Requires Firebase authentication and authorization for the specific patient.
    This endpoint does NOT require MCP - it reads directly from the patient database.
    """
    # Check patient access
    await require_patient_access(user, patient_id)

    try:
        # Query patient database directly (no MCP dependency)
        patient = await get_patient_from_db(patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        return {"patient": patient}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PATIENT_GET error patient_id={patient_id} uid={user.uid}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get patient details.")


@app.post("/api/chat", response_model=ChatResponse)
async def chat(chat_request: ChatRequest, user: FirebaseUser = Depends(require_firebase_auth)):
    """
    Process a chat message through the Claude agent.

    Requires Firebase authentication and authorization for the requested patient.
    """
    logger.info(f"CHAT_REQUEST received uid={user.uid} patient_id={chat_request.patient_id} message_len={len(chat_request.message)}")

    # Check patient access before any tool calls
    await require_patient_access(user, chat_request.patient_id)

    # Ensure MCP connection (auto-reconnect if needed)
    logger.info(f"CHAT_ENSURE_CONNECTED start uid={user.uid}")
    try:
        client, chat_agent = await ensure_mcp_connected()
        mcp_ready = client is not None and client.is_connected
        logger.info(f"CHAT_ENSURE_CONNECTED end mcp_ready={mcp_ready} uid={user.uid}")
    except HTTPException as e:
        logger.error(f"CHAT_ENSURE_CONNECTED failed uid={user.uid} status={e.status_code} detail={e.detail}")
        raise

    logger.info(f"CHAT_MCP_READY ready={mcp_ready} uid={user.uid} patient_id={chat_request.patient_id}")

    debug_logger = get_debug_logger()
    debug_logger.log_agent_reasoning({
        "action": "chat_request",
        "patient_id": chat_request.patient_id,
        "message_preview": chat_request.message[:100],
        "uid": user.uid,
        "mcp_ready": mcp_ready,
    })

    try:
        # Create a simple auth context for MCP tools (using UID now)
        from backend.auth_context import AuthContext
        import uuid
        auth = AuthContext(
            sub=user.uid,
            request_id=str(uuid.uuid4()),
            actor_type="human",
        )

        result = await chat_agent.chat(
            message=chat_request.message,
            patient_id=chat_request.patient_id,
            patient_name=chat_request.patient_name,
            conversation_history=chat_request.conversation_history or [],
            auth=auth,
        )
        return ChatResponse(**result)
    except MCPConnectionError as e:
        logger.error(f"CHAT_MCP_ERROR uid={user.uid} error={e}")
        invalidate_mcp_connection()
        raise HTTPException(
            status_code=503,
            detail={
                "error": "MCP_UNAVAILABLE",
                "message": "MCP server not connected",
                "reason": "mcp_disconnected",
            }
        )
    except Exception as e:
        if is_connection_error(e):
            logger.error(f"CHAT_MCP_ERROR uid={user.uid} error={e}")
            invalidate_mcp_connection()
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "MCP_UNAVAILABLE",
                    "message": "MCP server not connected",
                    "reason": "mcp_disconnected",
                }
            )
        logger.error(f"CHAT_ERROR uid={user.uid} error={e}")
        debug_logger.log_error({"error": str(e), "type": "chat_error", "uid": user.uid})
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


@app.get("/api/access")
async def get_current_user_access(user: FirebaseUser = Depends(require_firebase_auth)):
    """
    Get the current user's UID and allowed patient IDs.

    This is a quick debug endpoint to check authorization mapping.
    """
    allowed = await get_allowed_patient_ids(user.uid)
    logger.info(f"ACCESS_CHECK uid={user.uid} allowed_patient_ids={allowed}")
    return {
        "uid": user.uid,
        "allowed_patient_ids": allowed,
    }


@app.get("/api/debug/patient-access")
async def get_patient_access_list(user: FirebaseUser = Depends(require_firebase_auth)):
    """
    Debug endpoint to view all patient access mappings.
    Returns current user's UID and their allowed patients, plus all mappings.
    """
    from backend.patient_access import list_all_access
    all_access = await list_all_access()
    allowed = await get_allowed_patient_ids(user.uid)
    return {
        "current_uid": user.uid,
        "allowed_patient_ids": allowed,
        "all_mappings": all_access,
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
