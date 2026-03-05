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
    get_all_patients,
)
from backend import db

# Load config
config = get_config()

# Debug mode from environment (default false for production safety)
DEBUG_MODE = os.environ.get("DEBUG", "false").lower() == "true"

# Setup logging - prefer LOG_LEVEL env var, fallback to config
log_level = os.environ.get("LOG_LEVEL", config.get("logging", {}).get("level", "INFO")).upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
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
    logger.info(f"DEBUG_MODE={DEBUG_MODE} LOG_LEVEL={log_level}")

    # Log SDK versions for debugging compatibility issues
    try:
        import anthropic
        import httpx
        logger.info(f"SDK_VERSIONS anthropic={anthropic.__version__} httpx={httpx.__version__}")
    except Exception as e:
        logger.warning(f"Could not log SDK versions: {e}")

    # Initialize PostgreSQL connection pool and seed database on startup.
    # On Cloud Run, containers are stateless so this runs every time.
    # The seed data is idempotent — same patients, same data, every time.
    try:
        from scripts.seed_database import init_database as init_schema, seed_patients, seed_medical_records, seed_prescriptions, seed_appointments, seed_insurance, seed_lab_results
        logger.info("Initializing PostgreSQL connection pool...")
        await db.init_pool()
        logger.info("Creating database schema...")
        await init_schema()
        logger.info("Seeding patient database...")
        await seed_patients()
        await seed_medical_records()
        await seed_prescriptions()
        await seed_appointments()
        await seed_insurance()
        await seed_lab_results()
        patient_count = await db.fetchval("SELECT COUNT(*) FROM patients")
        logger.info(f"Patient database ready with {patient_count} patients.")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise
    
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
    await db.close_pool()
    logger.info("PostgreSQL connection pool closed.")


# Create FastAPI app
app = FastAPI(
    title="My Health Access API",
    description="Backend API for My Health Access healthcare demo (Firebase Auth only)",
    version="2.0.0",
    lifespan=lifespan,
    debug=DEBUG_MODE,
)

# CORS middleware
if config.get("backend", {}).get("cors_enabled", True):
    # Load allowed origins from config (wildcard + credentials is invalid per CORS spec)
    cors_origins = config.get("cors", {}).get(
        "allowed_origins",
        ["http://localhost:8080", "http://localhost:3000"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
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
    system_prompt_level: str | None = None      
    deterministic_error_responses: bool | None = None


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


@app.post("/api/auth/bootstrap")
async def bootstrap_user(
    user: FirebaseUser = Depends(get_current_user),
):
    """
    Bootstrap a new user with default claims.

    Called by the frontend immediately after sign-in when the user has no
    custom claims. Assigns a random patient_id and the 'patient' role.

    This is idempotent — calling it multiple times for a user who already
    has claims will not overwrite them.

    Returns:
        bootstrapped: true if new claims were assigned, false if user already had claims
        role: the user's role
        patient_ids: the user's assigned patient IDs
        patient_name: the assigned patient's name (only if bootstrapped=true)
    """
    import random

    # Check if user already has claims
    try:
        existing_user = firebase_admin_auth.get_user(user.uid)
        existing_claims = existing_user.custom_claims or {}

        if existing_claims.get("role"):
            # User already has claims — return without modifying
            logger.info(
                f"USER_BOOTSTRAP skip uid={user.uid} email={user.email} "
                f"reason=already_has_claims role={existing_claims.get('role')}"
            )
            return {
                "bootstrapped": False,
                "message": "User already has claims",
                "role": existing_claims.get("role"),
                "patient_ids": existing_claims.get("patient_ids", []),
            }
    except Exception as e:
        logger.error(f"USER_BOOTSTRAP error checking claims uid={user.uid}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "INTERNAL_ERROR", "message": "Failed to check user claims."}
        )

    # New user — assign random patient and 'patient' role
    patient_id = random.randint(1, 5)
    new_claims = {
        "role": "patient",
        "patient_ids": [patient_id],
        "actor_type": "human",
    }

    try:
        firebase_admin_auth.set_custom_user_claims(user.uid, new_claims)
    except Exception as e:
        logger.error(f"USER_BOOTSTRAP error setting claims uid={user.uid}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "INTERNAL_ERROR", "message": "Failed to set user claims."}
        )

    # Fetch patient name for the welcome modal
    patient_name = "a demo patient"
    try:
        patient = await get_patient_from_db(patient_id)
        if patient:
            patient_name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip()
    except Exception as e:
        logger.warning(f"USER_BOOTSTRAP could not fetch patient name: {e}")

    logger.info(
        f"USER_BOOTSTRAP uid={user.uid} email={user.email} "
        f"assigned_patient_id={patient_id}"
    )

    return {
        "bootstrapped": True,
        "role": "patient",
        "patient_ids": [patient_id],
        "patient_name": patient_name,
        "message": "Access granted",
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
    """
    Health check endpoint (public).

    Used by Cloud Run to determine container readiness.
    Returns 200 OK if the API process is healthy.
    Does not depend on MCP connection — that's reported separately.
    """
    mcp_connected = mcp_client is not None and getattr(mcp_client, "is_connected", False)
    return {
        "status": "healthy",
        "service": "health-advisor-api",
        "version": "2.0.0",
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


@app.get("/api/admin/users")
async def lookup_user_by_email(
    email: str,
    admin: AuthContext = Depends(require_admin_role),
):
    """
    Look up a Firebase user by email address.

    Requires admin role.

    Args:
        email: Email address to search for

    Returns:
        User info including uid, email, and current claims

    Raises:
        404 if no user exists with the given email
    """
    try:
        user = firebase_admin_auth.get_user_by_email(email)
    except firebase_admin_auth.UserNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "USER_NOT_FOUND",
                "message": f"No user found with email: {email}",
            }
        )
    except Exception as e:
        logger.error(f"ADMIN_LOOKUP_USER error email={email} admin_sub={admin.sub}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "INTERNAL_ERROR",
                "message": "Failed to look up user.",
            }
        )

    claims = user.custom_claims or {}

    logger.info(
        f"ADMIN_LOOKUP_USER admin_sub={admin.sub} email={email} "
        f"found_uid={user.uid} role={claims.get('role')}"
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


class UpdateRoleRequest(BaseModel):
    """Request body for updating user role."""
    role: str

    @validator("role")
    def role_must_be_valid(cls, v):
        valid = {"patient", "caregiver", "clinician", "admin"}
        if v not in valid:
            raise ValueError(f"role must be one of {valid}")
        return v


class UpdatePatientsRequest(BaseModel):
    """Request body for updating user patient access."""
    patient_ids: list[int]


@app.put("/api/admin/users/{uid}/role")
async def update_user_role(
    uid: str,
    body: UpdateRoleRequest,
    admin: AuthContext = Depends(require_admin_role),
):
    """
    Update only the role claim for a Firebase user.

    Preserves existing patient_ids and actor_type claims.
    Requires admin role.

    Args:
        uid: Firebase UID of the target user
        body: UpdateRoleRequest with new role

    Returns:
        Updated claims
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
        logger.error(f"ADMIN_UPDATE_ROLE lookup error uid={uid} admin_sub={admin.sub}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "INTERNAL_ERROR",
                "message": "Failed to retrieve user.",
            }
        )

    # Preserve existing claims, update role
    existing_claims = user.custom_claims or {}
    new_claims = {
        "role": body.role,
        "patient_ids": existing_claims.get("patient_ids", []),
        "actor_type": existing_claims.get("actor_type", "human"),
    }

    try:
        firebase_admin_auth.set_custom_user_claims(uid, new_claims)
    except Exception as e:
        logger.error(f"ADMIN_UPDATE_ROLE error uid={uid} admin_sub={admin.sub}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "INTERNAL_ERROR",
                "message": "Failed to update role.",
            }
        )

    logger.info(
        f"ADMIN_UPDATE_ROLE admin_sub={admin.sub} target_uid={uid} "
        f"old_role={existing_claims.get('role')} new_role={body.role}"
    )

    return {
        "uid": uid,
        "email": user.email,
        "claims": new_claims,
        "message": "Role updated. User must sign out and sign back in for changes to take effect.",
    }


@app.put("/api/admin/users/{uid}/patients")
async def update_user_patients(
    uid: str,
    body: UpdatePatientsRequest,
    admin: AuthContext = Depends(require_admin_role),
):
    """
    Update only the patient_ids claim for a Firebase user.

    Preserves existing role and actor_type claims.
    Requires admin role.

    Args:
        uid: Firebase UID of the target user
        body: UpdatePatientsRequest with new patient_ids list

    Returns:
        Updated claims
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
        logger.error(f"ADMIN_UPDATE_PATIENTS lookup error uid={uid} admin_sub={admin.sub}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "INTERNAL_ERROR",
                "message": "Failed to retrieve user.",
            }
        )

    # Preserve existing claims, update patient_ids
    existing_claims = user.custom_claims or {}
    new_claims = {
        "role": existing_claims.get("role", "patient"),
        "patient_ids": body.patient_ids,
        "actor_type": existing_claims.get("actor_type", "human"),
    }

    try:
        firebase_admin_auth.set_custom_user_claims(uid, new_claims)
    except Exception as e:
        logger.error(f"ADMIN_UPDATE_PATIENTS error uid={uid} admin_sub={admin.sub}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "INTERNAL_ERROR",
                "message": "Failed to update patient access.",
            }
        )

    logger.info(
        f"ADMIN_UPDATE_PATIENTS admin_sub={admin.sub} target_uid={uid} "
        f"old_patient_ids={existing_claims.get('patient_ids')} new_patient_ids={body.patient_ids}"
    )

    return {
        "uid": uid,
        "email": user.email,
        "claims": new_claims,
        "message": "Patient access updated. User must sign out and sign back in for changes to take effect.",
    }


@app.delete("/api/admin/users/{uid}/claims")
async def clear_user_claims(
    uid: str,
    admin: AuthContext = Depends(require_admin_role),
):
    """
    Clear all custom claims for a Firebase user.

    This resets the user to an unclaimed state. They will go through
    the bootstrap flow on next sign-in.
    Requires admin role.

    Args:
        uid: Firebase UID of the target user

    Returns:
        Confirmation of cleared claims
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
        logger.error(f"ADMIN_CLEAR_CLAIMS lookup error uid={uid} admin_sub={admin.sub}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "INTERNAL_ERROR",
                "message": "Failed to retrieve user.",
            }
        )

    # Store old claims for logging
    old_claims = user.custom_claims or {}

    # Clear claims by setting to empty dict
    try:
        firebase_admin_auth.set_custom_user_claims(uid, {})
    except Exception as e:
        logger.error(f"ADMIN_CLEAR_CLAIMS error uid={uid} admin_sub={admin.sub}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "INTERNAL_ERROR",
                "message": "Failed to clear claims.",
            }
        )

    logger.warning(
        f"ADMIN_CLEAR_CLAIMS admin_sub={admin.sub} target_uid={uid} "
        f"email={user.email} cleared_role={old_claims.get('role')} "
        f"cleared_patient_ids={old_claims.get('patient_ids')}"
    )

    return {
        "uid": uid,
        "email": user.email,
        "claims": {},
        "message": "Claims cleared. User will go through bootstrap on next sign-in.",
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
        "system_prompt_level",
        "deterministic_error_responses"
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
# Demo Endpoints (Security Control Demonstration)
# ============================================================================
#
# These endpoints demonstrate the authentication_required security control.
# They do NOT use standard auth dependencies. Instead, they check the toggle
# themselves and decide whether to require auth at the route level.
#
# This models a common real-world misconfiguration: a route added without
# the authentication middleware dependency, or a dev shortcut that made
# it to production.

def demo_auth_check(request: Request) -> FirebaseUser | None:
    """
    Conditional auth check for demo endpoints.

    When authentication_required is enabled: behaves like a normal
    protected route — requires a valid Firebase token.
    When authentication_required is disabled: skips auth entirely
    and returns None, allowing unauthenticated access.

    Logs the auth decision so it appears in the debug panel.
    """
    from backend.config import is_security_control_enabled

    if is_security_control_enabled("authentication_required"):
        # Auth is required — enforce it
        auth = getattr(request.state, "auth", None)
        user = getattr(request.state, "user", None)
        if auth is None or user is None:
            logger.warning(
                f"DEMO_ENDPOINT DENY path={request.url.path} "
                f"reason=authentication_required_enabled"
            )
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "AUTH_REQUIRED",
                    "message": "Authentication required.",
                    "reason": "authentication_required_enabled",
                }
            )
        return user
    else:
        # Auth is disabled — log the insecure state and allow through
        client_host = request.client.host if request.client else "unknown"
        logger.warning(
            f"DEMO_ENDPOINT ALLOW path={request.url.path} "
            f"reason=authentication_required_disabled "
            f"remote={client_host} "
            "THIS IS AN INSECURE CONFIGURATION"
        )
        return None


@app.get("/api/demo/status")
async def demo_status():
    """
    Demo endpoint: Shows the current authentication state for demo endpoints.

    Always public — no auth required. Use this to check whether the
    authentication_required toggle is on or off.
    """
    from backend.config import is_security_control_enabled

    auth_required = is_security_control_enabled("authentication_required")

    return {
        "demo_endpoints": {
            "authentication_required": auth_required,
            "base_url": "/api/demo",
            "endpoints": [
                "GET /api/demo/patients",
                "GET /api/demo/patients/{patient_id}",
                "GET /api/demo/status"
            ],
            "attack_demonstration": (
                "When authentication_required is false, the patients endpoints "
                "return PHI without any credentials. This demonstrates a common "
                "misconfiguration where a route is deployed without proper "
                "authentication middleware."
            ),
        }
    }


@app.get("/api/demo/patients")
async def demo_list_patients(request: Request):
    """
    Demo endpoint: List all patients.

    Behavior is controlled by the authentication_required security toggle:
    - Toggle ON: requires Firebase authentication (same as /api/patients)
    - Toggle OFF: returns all patients with no authentication required

    This demonstrates the attack vector of an unprotected endpoint
    leaking PHI. Use with the authentication_required toggle in /settings.
    """
    from backend.config import is_security_control_enabled

    user = demo_auth_check(request)
    auth_enforced = is_security_control_enabled("authentication_required")

    if user is None:
        # Auth disabled — return ALL patients (the attack scenario)
        try:
            patients = await get_all_patients()
            response_data = {
                "patients": patients,
                "count": len(patients),
                "auth_enforced": False,
                "warning": "Authentication disabled — this endpoint is publicly accessible",
            }
            response = JSONResponse(content=response_data)
            response.headers["X-Security-Warning"] = "Authentication disabled"
            return response
        except Exception as e:
            logger.error(f"DEMO_LIST_PATIENTS error: {e}")
            raise HTTPException(status_code=500, detail="Failed to load patients.")
    else:
        # Auth enabled — build AuthContext and return only authorized patients
        auth = build_auth_context(user)
        logger.info(f"DEMO_LIST_PATIENTS sub={auth.sub} role={auth.role} patient_ids={auth.patient_ids}")

        if not auth.patient_ids:
            return {
                "patients": [],
                "count": 0,
                "auth_enforced": True,
            }

        try:
            patients = await get_patients_by_ids(auth.patient_ids)
            return {
                "patients": patients,
                "count": len(patients),
                "auth_enforced": True,
            }
        except Exception as e:
            logger.error(f"DEMO_LIST_PATIENTS error sub={auth.sub}: {e}")
            raise HTTPException(status_code=500, detail="Failed to load patients.")


@app.get("/api/demo/patients/{patient_id}")
async def demo_get_patient(patient_id: int, request: Request):
    """
    Demo endpoint: Get a specific patient by ID.

    Same authentication behavior as /api/demo/patients.
    Toggle OFF: returns the patient record with no auth required.
    Toggle ON: requires auth and checks patient access via claims.
    """
    from backend.config import is_security_control_enabled

    user = demo_auth_check(request)
    auth_enforced = is_security_control_enabled("authentication_required")

    if user is None:
        # Auth disabled — return the patient regardless of who is asking
        try:
            patient = await get_patient_from_db(patient_id)
            if not patient:
                raise HTTPException(status_code=404, detail="Patient not found")

            response_data = {
                "patient": patient,
                "auth_enforced": False,
                "warning": "Authentication disabled — this endpoint is publicly accessible",
            }
            response = JSONResponse(content=response_data)
            response.headers["X-Security-Warning"] = "Authentication disabled"
            return response
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"DEMO_GET_PATIENT error patient_id={patient_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to get patient details.")
    else:
        # Auth enabled — check authorization via claims
        auth = build_auth_context(user)

        if not auth.can_access_patient(patient_id):
            logger.warning(
                f"DEMO_GET_PATIENT AUTHZ_DENIED sub={auth.sub} role={auth.role} "
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

        try:
            patient = await get_patient_from_db(patient_id)
            if not patient:
                raise HTTPException(status_code=404, detail="Patient not found")

            return {
                "patient": patient,
                "auth_enforced": True,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"DEMO_GET_PATIENT error patient_id={patient_id} sub={auth.sub}: {e}")
            raise HTTPException(status_code=500, detail="Failed to get patient details.")


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


@app.get("/settings")
async def serve_settings():
    """Serve the settings page."""
    settings_path = frontend_path / "settings.html"
    if settings_path.exists():
        return FileResponse(settings_path)
    return JSONResponse(
        {"error": "Settings page not found. Place settings.html in frontend/"},
        status_code=404,
    )


@app.get("/learn")
async def serve_learn():
    """Serve the learn page (placeholder)."""
    from fastapi.responses import HTMLResponse

    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Learn - My Health Access</title>
    <link rel="stylesheet" href="/static/css/styles.css">
</head>
<body>
    <div class="learn-container">
        <header class="learn-header">
            <h1>Learn — Security Scenarios</h1>
            <nav class="learn-header-nav">
                <a href="/" class="nav-link">App</a>
                <a href="/settings" class="nav-link">Settings</a>
                <a href="/debug" class="nav-link">Debug</a>
                <a href="/learn" class="nav-link nav-link-active">Learn</a>
            </nav>
        </header>
        <main class="learn-content">
            <div class="learn-card">
                <div class="learn-icon">📚</div>
                <h2>Coming Soon</h2>
                <p>
                    Security scenario walkthroughs are coming soon.
                    This section will contain guided exercises for exploring
                    authentication, authorization, prompt injection, and other
                    AI security topics.
                </p>
                <div class="learn-links">
                    <a href="/" class="learn-link">Back to App</a>
                    <a href="/settings" class="learn-link">Security Settings</a>
                </div>
            </div>
        </main>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content)


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    # Support environment variable overrides for Cloud Run
    # PORT is the standard Cloud Run env var
    host = os.environ.get("BACKEND_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("BACKEND_PORT", 8080)))

    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=DEBUG_MODE,
    )
