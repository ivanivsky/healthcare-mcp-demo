"""
Firebase Authentication utilities for My Health Access.

Provides Firebase Admin SDK initialization, ID token verification,
and auth middleware for protecting API endpoints with Google Sign-In.

Authorization is derived entirely from Firebase custom claims in the JWT.
NO session/cookie auth, NO database lookups for authorization.
"""

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from google.auth import exceptions as google_auth_exceptions

from backend.auth_context import AuthContext
from backend.config import is_security_control_enabled

logger = logging.getLogger("firebase_auth")


@dataclass
class FirebaseUser:
    """
    Verified Firebase user identity with authorization data from custom claims.

    The JWT token IS the authorization — no database lookup needed.
    """
    uid: str
    email: str | None
    email_verified: bool
    name: str | None = None
    picture: str | None = None
    role: str = "patient"  # Extracted from custom claims
    patient_ids: list[int] = field(default_factory=list)  # Extracted from custom claims
    actor_type: str = "human"  # Always "human" for user tokens
    raw_claims: dict = field(default_factory=dict)  # Full custom claims for debugging


# Public routes that don't require authentication
PUBLIC_ROUTES = {
    "/",
    "/debug",
    "/api/config",
    "/api/health",
    "/api/debug/events",
    "/api/debug/config",
    "/settings",
    "/learn"
}

# Route prefixes that are always public (static files, demo endpoints)
# Demo endpoints handle their own auth based on the authentication_required toggle
PUBLIC_PREFIXES = (
    "/static/",
    "/api/demo/",
)


# Module-level state
_firebase_app: Optional[firebase_admin.App] = None


def init_firebase() -> bool:
    """
    Initialize Firebase Admin SDK.

    Supports:
    - GOOGLE_APPLICATION_CREDENTIALS env var (path to service account JSON)
    - FIREBASE_SERVICE_ACCOUNT env var (JSON string of service account)
    - Application Default Credentials (on Cloud Run)

    Requires project ID from:
    - FIREBASE_PROJECT_ID env var (priority)
    - GOOGLE_CLOUD_PROJECT env var (fallback)

    Returns:
        True if initialized successfully, False otherwise
    """
    global _firebase_app

    if _firebase_app is not None:
        return True

    # Get project ID (required for token verification)
    project_id = os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        logger.error("FIREBASE_INIT failed: No project ID found")
        logger.error("Fix: export FIREBASE_PROJECT_ID=your-project-id")
        raise RuntimeError(
            "Firebase project ID required. Set FIREBASE_PROJECT_ID or GOOGLE_CLOUD_PROJECT env var."
        )

    # Options dict with explicit project ID
    options = {"projectId": project_id}

    try:
        # Option 1: Service account JSON from env var (as string)
        sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if sa_json:
            sa_dict = json.loads(sa_json)
            cred = credentials.Certificate(sa_dict)
            _firebase_app = firebase_admin.initialize_app(cred, options=options)
            logger.info(f"FIREBASE_INIT project_id={project_id} credential_source=env")
            return True

        # Option 2: GOOGLE_APPLICATION_CREDENTIALS (path to file)
        gac_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if gac_path:
            cred = credentials.Certificate(gac_path)
            _firebase_app = firebase_admin.initialize_app(cred, options=options)
            logger.info(f"FIREBASE_INIT project_id={project_id} credential_source=file")
            return True

        # Option 3: Application Default Credentials (Cloud Run or local gcloud auth)
        # Check if ADC is likely available before initializing
        _check_adc_availability()
        _firebase_app = firebase_admin.initialize_app(options=options)
        logger.info(f"FIREBASE_INIT project_id={project_id} credential_source=adc")
        return True

    except google_auth_exceptions.DefaultCredentialsError as e:
        logger.error(f"FIREBASE_INIT failed: DefaultCredentialsError - {e}")
        logger.error("Fix: Run 'gcloud auth application-default login' OR set GOOGLE_APPLICATION_CREDENTIALS")
        return False
    except Exception as e:
        logger.warning(f"Firebase Admin SDK initialization failed: {e}")
        logger.warning("Token verification will not be available")
        return False


def _check_adc_availability() -> None:
    """
    Check if Application Default Credentials are likely available.
    Logs a warning at startup if running locally without ADC.
    """
    # Check if we're likely running locally (not on Cloud Run)
    is_cloud_run = os.environ.get("K_SERVICE") is not None
    if is_cloud_run:
        return  # ADC is automatic on Cloud Run

    # Check for ADC file in default location
    adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    if not os.path.exists(adc_path):
        logger.warning("FIREBASE_INIT local dev: ADC not found at ~/.config/gcloud/application_default_credentials.json")
        logger.warning("FIREBASE_INIT local dev: Run 'gcloud auth application-default login' to authenticate")


def verify_id_token(id_token: str) -> FirebaseUser:
    """
    Verify a Firebase ID token and return the user identity with claims.

    Extracts custom claims (role, patient_ids) from the verified token.
    If custom claims are absent, returns a valid user with default values
    (role="patient", patient_ids=[]) — this is default-deny, not an error.

    Args:
        id_token: The Firebase ID token from the client

    Returns:
        FirebaseUser with verified identity and authorization from claims

    Raises:
        HTTPException: If token is invalid, expired, or verification fails
    """
    if _firebase_app is None:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "AUTH_NOT_CONFIGURED",
                "message": "Authentication service not configured."
            }
        )

    try:
        decoded = firebase_auth.verify_id_token(id_token)

        # Extract custom claims (everything except standard fields)
        standard_fields = {
            "uid", "email", "email_verified", "name", "picture",
            "iss", "aud", "auth_time", "iat", "exp", "sub", "firebase"
        }
        raw_claims = {k: v for k, v in decoded.items() if k not in standard_fields}

        # Extract authorization fields from custom claims with defaults
        role = raw_claims.get("role", "patient")
        patient_ids_raw = raw_claims.get("patient_ids", [])

        # Ensure patient_ids is a list of integers
        if isinstance(patient_ids_raw, list):
            patient_ids = [int(pid) for pid in patient_ids_raw if isinstance(pid, (int, str))]
        else:
            patient_ids = []

        # actor_type is always "human" for user tokens
        actor_type = raw_claims.get("actor_type", "human")

        return FirebaseUser(
            uid=decoded["uid"],
            email=decoded.get("email"),
            email_verified=decoded.get("email_verified", False),
            name=decoded.get("name"),
            picture=decoded.get("picture"),
            role=role,
            patient_ids=patient_ids,
            actor_type=actor_type,
            raw_claims=raw_claims,
        )

    except firebase_auth.ExpiredIdTokenError as e:
        logger.warning(f"VERIFY_TOKEN failed: ExpiredIdTokenError {e}")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "TOKEN_EXPIRED",
                "message": "Authentication token has expired.",
                "reason": "token_expired",
                "error_code": "ExpiredIdTokenError"
            }
        )
    except firebase_auth.RevokedIdTokenError as e:
        logger.warning(f"VERIFY_TOKEN failed: RevokedIdTokenError {e}")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "TOKEN_REVOKED",
                "message": "Authentication token has been revoked.",
                "reason": "token_revoked",
                "error_code": "RevokedIdTokenError"
            }
        )
    except firebase_auth.InvalidIdTokenError as e:
        logger.warning(f"VERIFY_TOKEN failed: InvalidIdTokenError {e}")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "INVALID_TOKEN",
                "message": "Authentication token is invalid.",
                "reason": "invalid_token",
                "error_code": "InvalidIdTokenError"
            }
        )
    except google_auth_exceptions.DefaultCredentialsError as e:
        logger.error(f"VERIFY_TOKEN failed: DefaultCredentialsError {e}")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "AUTH_REQUIRED",
                "message": "Backend credentials missing. Run: gcloud auth application-default login OR set GOOGLE_APPLICATION_CREDENTIALS to a service account JSON.",
                "reason": "missing_adc",
                "error_code": "DefaultCredentialsError"
            }
        )
    except Exception as e:
        error_class = type(e).__name__
        logger.error(f"VERIFY_TOKEN failed: {error_class} {e}")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "AUTH_REQUIRED",
                "message": "Authentication required.",
                "reason": "token_verification_failed",
                "error_code": error_class
            }
        )


def build_auth_context(user: FirebaseUser, request_id: str | None = None) -> AuthContext:
    """
    Build an AuthContext from a verified FirebaseUser.

    This is the single place where a verified FirebaseUser becomes an AuthContext.
    It should never be inlined — always call this function.

    Args:
        user: Verified FirebaseUser from token verification
        request_id: Optional correlation ID for tracing (generated if not provided)

    Returns:
        AuthContext with authorization data from the user's claims
    """
    return AuthContext(
        sub=user.uid,
        request_id=request_id or str(uuid.uuid4()),
        actor_type=user.actor_type,
        role=user.role,
        patient_ids=user.patient_ids,
    )


def get_bearer_token(request: Request) -> Optional[str]:
    """
    Extract Bearer token from Authorization header.

    Args:
        request: FastAPI request object

    Returns:
        The token string, or None if not present/invalid format
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None

    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None

    return parts[1]


def require_firebase_auth(request: Request) -> FirebaseUser:
    """
    FastAPI dependency that requires valid Firebase authentication.

    Returns a FirebaseUser with role and patient_ids populated from claims.

    Usage:
        @app.get("/api/protected")
        async def protected(user: FirebaseUser = Depends(require_firebase_auth)):
            return {"uid": user.uid, "role": user.role}

    Args:
        request: FastAPI request object

    Returns:
        FirebaseUser with verified identity and authorization from claims

    Raises:
        HTTPException: 401 if authentication is missing or invalid
    """
    path = request.url.path
    auth_header = request.headers.get("Authorization")

    # Debug logging for troubleshooting
    logger.info(f"FIREBASE_AUTH path={path} auth_header_present={auth_header is not None}")

    # Check if Authorization header exists
    if not auth_header:
        logger.warning(f"FIREBASE_AUTH DENY path={path} reason=missing_authorization_header")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "AUTH_REQUIRED",
                "message": "Authentication required.",
                "reason": "missing_authorization_header"
            }
        )

    # Check if it's Bearer scheme
    parts = auth_header.split(None, 1)  # Split on whitespace, max 2 parts
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.warning(f"FIREBASE_AUTH DENY path={path} reason=invalid_authorization_scheme scheme={parts[0] if parts else 'empty'}")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "AUTH_REQUIRED",
                "message": "Authentication required.",
                "reason": "invalid_authorization_scheme"
            }
        )

    token = parts[1]
    token_preview = token[:20] if len(token) > 20 else token
    logger.info(f"FIREBASE_AUTH path={path} token_len={len(token)} token_preview={token_preview}...")

    # Attempt token verification
    try:
        user = verify_id_token(token)
        logger.info(f"FIREBASE_AUTH ALLOW path={path} uid={user.uid} email={user.email} role={user.role} patient_ids={user.patient_ids}")
        return user
    except HTTPException:
        # Re-raise HTTPExceptions from verify_id_token as-is
        raise
    except Exception as e:
        error_class = type(e).__name__
        logger.warning(f"FIREBASE_AUTH verify failed: {error_class} {e}")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "AUTH_REQUIRED",
                "message": "Authentication required.",
                "reason": "token_verification_failed",
                "error_code": error_class
            }
        )


def log_auth_decision(
    path: str,
    uid: Optional[str],
    decision: str,
    deny_reason: Optional[str] = None,
) -> None:
    """
    Log authentication/authorization decision for audit trail.

    Args:
        path: Request path
        uid: User ID if available
        decision: "allow" or "deny"
        deny_reason: Reason for denial if decision is "deny"
    """
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": path,
        "uid": uid,
        "decision": decision,
    }

    if deny_reason:
        log_entry["deny_reason"] = deny_reason

    # Log as structured JSON for Cloud Run log aggregation
    logger.info(f"AUTH_DECISION: {json.dumps(log_entry)}")


def is_public_route(path: str) -> bool:
    """Check if a route is public (doesn't require authentication)."""
    if path in PUBLIC_ROUTES:
        return True
    for prefix in PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


class FirebaseAuthMiddleware(BaseHTTPMiddleware):
    """
    Firebase JWT authentication middleware.

    Validates Authorization: Bearer <token> header for all protected routes.
    Extracts role and patient_ids from custom claims and attaches to request.state.

    Authorization is derived entirely from the JWT claims — no database lookup.

    Public routes (/, /api/config, /api/health, /static/*) bypass auth.
    Protected routes return 401 JSON error if auth is missing/invalid.

    After middleware runs, access:
        - request.state.user: FirebaseUser (or None for public routes)
        - request.state.auth: AuthContext (or None for public routes)
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Public routes - no auth required
        if is_public_route(path):
            request.state.user = None
            request.state.auth = None
            return await call_next(request)

        # Protected routes - require valid JWT
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            logger.warning(f"AUTH_MIDDLEWARE DENY path={path} reason=missing_authorization_header")
            return JSONResponse(
                status_code=401,
                content={
                    "error": "AUTH_REQUIRED",
                    "message": "Authorization header required.",
                    "reason": "missing_authorization_header",
                }
            )

        # Parse Bearer token
        parts = auth_header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            logger.warning(f"AUTH_MIDDLEWARE DENY path={path} reason=invalid_authorization_scheme")
            return JSONResponse(
                status_code=401,
                content={
                    "error": "AUTH_REQUIRED",
                    "message": "Invalid Authorization header format. Use: Bearer <token>",
                    "reason": "invalid_authorization_scheme",
                }
            )

        token = parts[1]

        # Verify token and extract claims
        try:
            user = verify_id_token(token)
        except HTTPException as e:
            # verify_id_token returns HTTPException with proper error details
            return JSONResponse(
                status_code=e.status_code,
                content=e.detail if isinstance(e.detail, dict) else {"error": "AUTH_REQUIRED", "message": str(e.detail)}
            )
        except Exception as e:
            error_class = type(e).__name__
            logger.error(f"AUTH_MIDDLEWARE error path={path} error={error_class}: {e}")
            return JSONResponse(
                status_code=401,
                content={
                    "error": "AUTH_REQUIRED",
                    "message": "Authentication failed.",
                    "reason": "token_verification_error",
                    "error_code": error_class,
                }
            )

        # Build AuthContext from verified user
        auth = build_auth_context(user)

        # Attach to request state
        request.state.user = user
        request.state.auth = auth

        logger.info(
            f"AUTH_MIDDLEWARE ALLOW path={path} uid={user.uid} "
            f"role={user.role} patient_ids={user.patient_ids}"
        )

        return await call_next(request)


def get_current_user(request: Request) -> FirebaseUser:
    """
    FastAPI dependency to get the authenticated user from request.state.

    Use this in route handlers after FirebaseAuthMiddleware is applied.

    Usage:
        @app.get("/api/protected")
        async def protected(user: FirebaseUser = Depends(get_current_user)):
            return {"uid": user.uid}

    Raises:
        HTTPException 401 if user not authenticated (shouldn't happen if middleware is configured)
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "AUTH_REQUIRED",
                "message": "Authentication required.",
                "reason": "no_user_in_context",
            }
        )
    return user


def get_auth_context(request: Request) -> AuthContext:
    """
    FastAPI dependency to get the AuthContext from request.state.

    Use this in route handlers that need to make authorization decisions.

    Usage:
        @app.get("/api/patients/{patient_id}")
        async def get_patient(patient_id: int, auth: AuthContext = Depends(get_auth_context)):
            if not auth.can_access_patient(patient_id):
                raise HTTPException(403, "Forbidden")
            ...

    Raises:
        HTTPException 401 if user not authenticated
    """
    auth = getattr(request.state, "auth", None)
    if auth is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "AUTH_REQUIRED",
                "message": "Authentication required.",
                "reason": "no_auth_in_context",
            }
        )
    return auth
