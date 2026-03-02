"""
My Health Access Backend API

FastAPI application that serves as the MCP client and Claude agent orchestrator.
Uses Firebase Authentication exclusively - no session/cookie auth.

Authorization is derived entirely from Firebase custom claims in the JWT.
No database lookups for authorization decisions.
"""

import asyncio
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
from pydantic import BaseModel, validator
from firebase_admin import auth as firebase_admin_auth

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import (
    get_config,
    get_security_config,
    update_security_config,
    reset_security_config,
)
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
from backend.auth_context import AuthContext
from backend.firebase_auth import (
    init_firebase,
    get_current_user,
    get_auth_context,
    build_auth_context,
    log_auth_decision,
    FirebaseUser,
    FirebaseAuthMiddleware,
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

    # Log Firebase frontend config status (without exposing actual keys)
    fb_api_key = os.environ.get("FIREBASE_API_KEY")
    fb_auth_domain = os.environ.get("FIREBASE_AUTH_DOMAIN")
    if fb_api_key and fb_auth_domain:
        logger.info(f"FIREBASE_CONFIG: ready (apiKey={fb_api_key[:8]}..., authDomain={fb_auth_domain})")
    else:
        missing = []
        if not fb_api_key:
            missing.append("FIREBASE_API_KEY")
        if not fb_auth_domain:
            missing.append("FIREBASE_AUTH_DOMAIN")
        logger.warning(f"FIREBASE_CONFIG: missing [{', '.join(missing)}] - frontend will show config error")

    logger.info("Authorization: using Firebase custom claims (no patient_access.db)")

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

# Firebase Auth middleware - validates JWT on all protected routes
# Authorization is derived entirely from custom claims in the JWT
app.add_middleware(FirebaseAuthMiddleware)


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


class SetClaimsRequest(BaseModel):
    """Request body for setting Firebase custom claims."""
    role: str
    patient_ids: list[int] = []

    @validator("role")
    def role_must_be_valid(cls, v):
        valid = {"patient", "caregiver", "clinician", "admin"}
        if v not in valid:
            raise ValueError(f"role must be one of {valid}")
        return v


class SecurityConfigUpdate(BaseModel):
    """Partial update to security configuration.
    Only include the controls you want to change.
    """
    authentication_required: bool | None = None
    authorization_required: bool | None = None
    mcp_transport_auth_required: bool | None = None
    mcp_auth_context_signing_required: bool | None = None
    prompt_injection_protection: bool | None = None


class SecurityConfigResponse(BaseModel):
    """Response containing security configuration state."""
    controls: dict
    posture_summary: str  # e.g. "4 of 5 controls enabled"
    insecure_controls: list[str]  # names of any disabled controls


# ============================================================================
# Authorization Helpers
# ============================================================================

def require_patient_access(auth: AuthContext, patient_id: int) -> None:
    """
    Check if the AuthContext is authorized to access the given patient.

    Raises HTTPException 403 if not authorized.
    Authorization is derived entirely from the verified JWT claims — no DB lookup.

    Args:
        auth: AuthContext built from verified Firebase claims
        patient_id: The patient ID to check access for

    Raises:
        HTTPException 403 if access denied
    """
    if not auth.can_access_patient(patient_id):
        logger.warning(
            f"AUTHZ_DENIED sub={auth.sub} role={auth.role} "
            f"patient_id={patient_id} patient_ids={auth.patient_ids}"
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "FORBIDDEN",
                "message": "Not authorized for requested patient.",
                "patient_id": str(patient_id),
            }
        )
    logger.info(
        f"AUTHZ_ALLOWED sub={auth.sub} role={auth.role} "
        f"patient_id={patient_id}"
    )


def require_admin_role(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """
    Requires the caller to have role='admin' in their verified claims.

    Use as a FastAPI dependency for admin-only endpoints.
    """
    if auth.role != "admin":
        logger.warning(
            f"ADMIN_ACCESS_DENIED sub={auth.sub} role={auth.role} "
            f"reason=insufficient_role"
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "FORBIDDEN",
                "message": "Admin role required.",
                "reason": "insufficient_role",
            }
        )
    return auth


# ============================================================================
# Auth Endpoints (Firebase only)
# ============================================================================

@app.get("/api/whoami")
async def whoami(
    request: Request,
    user: FirebaseUser = Depends(get_current_user),
    auth: AuthContext = Depends(get_auth_context),
):
    """
    Get current Firebase-authenticated user identity.

    Requires valid Firebase ID token in Authorization header.
    Returns uid, email, role, patient_ids, and actor_type from claims.
    Does NOT return raw_claims (use /api/debug/patient-access for that).
    """
    log_auth_decision(
        path=request.url.path,
        uid=user.uid,
        decision="allow",
    )

    return {
        "uid": user.uid,
        "email": user.email,
        "email_verified": user.email_verified,
        "role": auth.role,
        "patient_ids": auth.patient_ids,
        "actor_type": auth.actor_type,
    }


# ============================================================================
# API Endpoints (Protected with Firebase Auth)
# ============================================================================

@app.get("/api/config")
async def get_frontend_config():
    """
    Get frontend configuration (public).

    Returns Firebase config from environment variables.
    Frontend calls this once on load to initialize Firebase SDK.
    """
    api_key = os.environ.get("FIREBASE_API_KEY")
    auth_domain = os.environ.get("FIREBASE_AUTH_DOMAIN")
    project_id = os.environ.get("FIREBASE_PROJECT_ID")

    # Check what's missing
    missing = []
    if not api_key:
        missing.append("FIREBASE_API_KEY")
    if not auth_domain:
        missing.append("FIREBASE_AUTH_DOMAIN")

    if missing:
        logger.error(f"CONFIG_MISSING: {', '.join(missing)} not set")
        return JSONResponse(
            status_code=500,
            content={
                "error": "CONFIG_MISSING",
                "missing": missing,
                "how_to_fix": "export FIREBASE_API_KEY='your-key'; export FIREBASE_AUTH_DOMAIN='your-project.firebaseapp.com'",
            }
        )

    firebase_config = {
        "apiKey": api_key,
        "authDomain": auth_domain,
    }

    if project_id:
        firebase_config["projectId"] = project_id

    return {"firebase": firebase_config}


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
async def list_patients(auth: AuthContext = Depends(get_auth_context)):
    """
    Get list of patients the authenticated user can access.

    Requires Firebase authentication. Returns only patients the user is authorized for.
    Authorization is derived from JWT custom claims — no database lookup.
    """
    logger.info(f"PATIENT_LIST sub={auth.sub} role={auth.role} patient_ids={auth.patient_ids}")

    if not auth.patient_ids:
        logger.info(f"PATIENT_LIST no_patient_access sub={auth.sub}")
        return []

    try:
        # Query patient database using patient_ids from claims
        patients = await get_patients_by_ids(auth.patient_ids)
        logger.info(f"PATIENT_LIST returned_count={len(patients)}")
        return patients
    except Exception as e:
        logger.error(f"PATIENT_LIST error sub={auth.sub}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load patients.")


@app.get("/api/patients/{patient_id}")
async def get_patient(patient_id: int, auth: AuthContext = Depends(get_auth_context)):
    """
    Get patient details.

    Requires Firebase authentication and authorization for the specific patient.
    Authorization is derived from JWT custom claims — no database lookup.
    """
    # Check patient access using claims-based authorization
    require_patient_access(auth, patient_id)

    try:
        # Query patient database directly (no MCP dependency)
        patient = await get_patient_from_db(patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found")
        return {"patient": patient}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PATIENT_GET error patient_id={patient_id} sub={auth.sub}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get patient details.")


@app.post("/api/chat", response_model=ChatResponse)
async def chat(
    chat_request: ChatRequest,
    user: FirebaseUser = Depends(get_current_user),
    auth: AuthContext = Depends(get_auth_context),
):
    """
    Process a chat message through the Claude agent.

    Requires Firebase authentication and authorization for the requested patient.
    Authorization is derived from JWT custom claims — no database lookup.
    """
    logger.info(
        f"CHAT_REQUEST received sub={auth.sub} role={auth.role} "
        f"patient_id={chat_request.patient_id} message_len={len(chat_request.message)}"
    )

    # Check patient access using claims-based authorization
    require_patient_access(auth, chat_request.patient_id)

    # Ensure MCP connection (auto-reconnect if needed)
    logger.info(f"CHAT_ENSURE_CONNECTED start sub={auth.sub}")
    try:
        client, chat_agent = await ensure_mcp_connected()
        mcp_ready = client is not None and client.is_connected
        logger.info(f"CHAT_ENSURE_CONNECTED end mcp_ready={mcp_ready} sub={auth.sub}")
    except HTTPException as e:
        logger.error(f"CHAT_ENSURE_CONNECTED failed sub={auth.sub} status={e.status_code} detail={e.detail}")
        raise

    logger.info(f"CHAT_MCP_READY ready={mcp_ready} sub={auth.sub} patient_id={chat_request.patient_id}")

    debug_logger = get_debug_logger()
    debug_logger.log_agent_reasoning({
        "action": "chat_request",
        "patient_id": chat_request.patient_id,
        "message_preview": chat_request.message[:100],
        "sub": auth.sub,
        "role": auth.role,
        "mcp_ready": mcp_ready,
    })

    try:
        # The AuthContext is already built from verified claims
        # Pass it directly to the agent — no inline construction
        result = await chat_agent.chat(
            message=chat_request.message,
            patient_id=chat_request.patient_id,
            patient_name=chat_request.patient_name,
            conversation_history=chat_request.conversation_history or [],
            auth=auth,
        )
        return ChatResponse(**result)
    except MCPConnectionError as e:
        logger.error(f"CHAT_MCP_ERROR sub={auth.sub} error={e}")
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
            logger.error(f"CHAT_MCP_ERROR sub={auth.sub} error={e}")
            invalidate_mcp_connection()
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "MCP_UNAVAILABLE",
                    "message": "MCP server not connected",
                    "reason": "mcp_disconnected",
                }
            )
        logger.error(f"CHAT_ERROR sub={auth.sub} error={e}")
        debug_logger.log_error({"error": str(e), "type": "chat_error", "sub": auth.sub})
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
    # Get runtime security config (includes any overrides)
    security_config = get_security_config()

    # Compute security posture
    control_keys = [
        "authentication_required",
        "authorization_required",
        "mcp_transport_auth_required",
        "mcp_auth_context_signing_required",
        "prompt_injection_protection",
    ]
    enabled_count = sum(1 for k in control_keys if security_config.get(k, False))
    total_count = len(control_keys)
    insecure = [k for k in control_keys if not security_config.get(k, False)]

    # Return safe config info (no secrets)
    return {
        "app": config.get("app", {}),
        "mcp": config.get("mcp", {}),
        "backend": {
            "host": config.get("backend", {}).get("host"),
            "port": config.get("backend", {}).get("port"),
        },
        "security": security_config,
        "security_posture": {
            "summary": f"{enabled_count} of {total_count} controls enabled",
            "insecure_controls": insecure,
        },
        "logging": config.get("logging", {}),
    }


@app.get("/api/access")
async def get_current_user_access(auth: AuthContext = Depends(get_auth_context)):
    """
    Get the current user's authorization from JWT claims.

    Returns role and patient_ids derived from custom claims.
    This is a quick debug endpoint to check authorization.
    """
    logger.info(f"ACCESS_CHECK sub={auth.sub} role={auth.role} patient_ids={auth.patient_ids}")
    return {
        "uid": auth.sub,
        "role": auth.role,
        "patient_ids": auth.patient_ids,
    }


@app.get("/api/debug/patient-access")
async def get_patient_access_debug(
    user: FirebaseUser = Depends(get_current_user),
    auth: AuthContext = Depends(get_auth_context),
):
    """
    Debug endpoint to view the current user's claims data.

    Returns uid, role, patient_ids, and raw_claims for debugging.
    This is user-scoped — shows only the authenticated user's data.
    """
    return {
        "uid": user.uid,
        "role": auth.role,
        "patient_ids": auth.patient_ids,
        "actor_type": auth.actor_type,
        "raw_claims": user.raw_claims,
    }


# ============================================================================
# Admin Endpoints (Require admin role)
# ============================================================================

@app.get("/api/admin/users/{uid}/claims")
async def get_user_claims(
    uid: str,
    admin: AuthContext = Depends(require_admin_role),
):
    """
    Get the current custom claims for a Firebase user.

    Requires admin role.

    Args:
        uid: Firebase UID of the target user

    Returns:
        uid, email, and current custom claims (role, patient_ids, actor_type)

    Raises:
        404 if the UID does not exist in Firebase
    """
    try:
        user = firebase_admin_auth.get_user(uid)
    except firebase_admin_auth.UserNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "USER_NOT_FOUND",
                "message": f"No user found with UID: {uid}",
            }
        )
    except Exception as e:
        logger.error(f"ADMIN_GET_CLAIMS error uid={uid} admin_sub={admin.sub}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "INTERNAL_ERROR",
                "message": "Failed to retrieve user.",
            }
        )

    claims = user.custom_claims or {}

    logger.info(
        f"ADMIN_GET_CLAIMS admin_sub={admin.sub} target_uid={uid} "
        f"role={claims.get('role')} patient_ids={claims.get('patient_ids')}"
    )

    return {
        "uid": user.uid,
        "email": user.email,
        "claims": {
            "role": claims.get("role"),
            "patient_ids": claims.get("patient_ids", []),
            "actor_type": claims.get("actor_type"),
        },
    }


@app.put("/api/admin/users/{uid}/claims")
async def set_user_claims(
    uid: str,
    body: SetClaimsRequest,
    admin: AuthContext = Depends(require_admin_role),
):
    """
    Set custom claims on a Firebase user.

    Requires admin role.

    Args:
        uid: Firebase UID of the target user
        body: SetClaimsRequest with role and patient_ids

    Returns:
        Confirmation with the claims that were set

    Raises:
        404 if the UID does not exist in Firebase
        400 if the role value is invalid (handled by Pydantic)
    """
    # Verify the target user exists
    try:
        user = firebase_admin_auth.get_user(uid)
    except firebase_admin_auth.UserNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "USER_NOT_FOUND",
                "message": f"No user found with UID: {uid}",
            }
        )
    except Exception as e:
        logger.error(f"ADMIN_SET_CLAIMS lookup error uid={uid} admin_sub={admin.sub}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "INTERNAL_ERROR",
                "message": "Failed to retrieve user.",
            }
        )

    # Build claims dict (always include actor_type: "human")
    claims = {
        "role": body.role,
        "patient_ids": body.patient_ids,
        "actor_type": "human",
    }

    # Set the claims
    try:
        firebase_admin_auth.set_custom_user_claims(uid, claims)
    except Exception as e:
        logger.error(f"ADMIN_SET_CLAIMS error uid={uid} admin_sub={admin.sub}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "INTERNAL_ERROR",
                "message": "Failed to set claims.",
            }
        )

    # Audit log
    logger.info(
        f"ADMIN_SET_CLAIMS admin_sub={admin.sub} target_uid={uid} "
        f"role={body.role} patient_ids={body.patient_ids}"
    )

    return {
        "uid": uid,
        "email": user.email,
        "claims": claims,
        "message": "Claims set successfully. User must sign out and sign back in for changes to take effect.",
    }


# ============================================================================
# Security Config Endpoints (Require admin role)
# ============================================================================

def _build_security_response(security_config: dict) -> SecurityConfigResponse:
    """Build a SecurityConfigResponse from the current security config."""
    control_keys = [
        "authentication_required",
        "authorization_required",
        "mcp_transport_auth_required",
        "mcp_auth_context_signing_required",
        "prompt_injection_protection",
    ]
    enabled_count = sum(1 for k in control_keys if security_config.get(k, False))
    total_count = len(control_keys)
    insecure = [k for k in control_keys if not security_config.get(k, False)]

    return SecurityConfigResponse(
        controls=security_config,
        posture_summary=f"{enabled_count} of {total_count} controls enabled",
        insecure_controls=insecure,
    )


@app.get("/api/admin/security-config", response_model=SecurityConfigResponse)
async def get_security_config_endpoint(
    admin: AuthContext = Depends(require_admin_role),
):
    """
    Get the current runtime security configuration.

    Requires admin role.

    Returns:
        Current security controls with posture summary
    """
    security_config = get_security_config()

    logger.info(f"ADMIN_GET_SECURITY_CONFIG admin_sub={admin.sub}")

    return _build_security_response(security_config)


@app.put("/api/admin/security-config", response_model=SecurityConfigResponse)
async def update_security_config_endpoint(
    body: SecurityConfigUpdate,
    admin: AuthContext = Depends(require_admin_role),
):
    """
    Apply partial updates to the runtime security configuration.

    Only fields included in the request body are updated.
    Changes are in-memory only — they reset to YAML defaults on restart.

    Requires admin role.

    Returns:
        Updated security controls with posture summary
    """
    # Get current config for comparison
    old_config = get_security_config()

    # Build updates dict from non-None fields
    updates = {}
    for field in [
        "authentication_required",
        "authorization_required",
        "mcp_transport_auth_required",
        "mcp_auth_context_signing_required",
        "prompt_injection_protection",
    ]:
        value = getattr(body, field)
        if value is not None:
            old_value = old_config.get(field)
            updates[field] = value

            # Log every change at WARNING level (high-significance event)
            if old_value != value:
                logger.warning(
                    f"SECURITY_CONFIG_CHANGE admin_sub={admin.sub} "
                    f"control={field} old={old_value} new={value}"
                )

    # Apply updates
    new_config = update_security_config(updates)

    return _build_security_response(new_config)


@app.post("/api/admin/security-config/reset", response_model=SecurityConfigResponse)
async def reset_security_config_endpoint(
    admin: AuthContext = Depends(require_admin_role),
):
    """
    Reset all runtime security overrides to YAML defaults.

    Requires admin role.

    Returns:
        Reset security controls with posture summary
    """
    logger.warning(f"SECURITY_CONFIG_RESET admin_sub={admin.sub}")

    new_config = reset_security_config()

    return _build_security_response(new_config)


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
