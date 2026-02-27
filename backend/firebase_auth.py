"""
Firebase Authentication utilities for My Health Access.

Provides Firebase Admin SDK initialization and ID token verification
for protecting API endpoints with Google Sign-In.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from fastapi import HTTPException, Request
from google.auth import exceptions as google_auth_exceptions

logger = logging.getLogger("firebase_auth")

@dataclass
class FirebaseUser:
    """Verified Firebase user identity."""
    uid: str
    email: Optional[str]
    email_verified: bool
    name: Optional[str] = None
    picture: Optional[str] = None
    claims: dict = None

    def __post_init__(self):
        if self.claims is None:
            self.claims = {}


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
    Verify a Firebase ID token and return the user identity.

    Args:
        id_token: The Firebase ID token from the client

    Returns:
        FirebaseUser with verified identity

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
        standard_fields = {"uid", "email", "email_verified", "name", "picture", "iss", "aud", "auth_time", "iat", "exp", "sub", "firebase"}
        claims = {k: v for k, v in decoded.items() if k not in standard_fields}

        return FirebaseUser(
            uid=decoded["uid"],
            email=decoded.get("email"),
            email_verified=decoded.get("email_verified", False),
            name=decoded.get("name"),
            picture=decoded.get("picture"),
            claims=claims,
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

    Usage:
        @app.get("/api/protected")
        async def protected(user: FirebaseUser = Depends(require_firebase_auth)):
            return {"uid": user.uid}

    Args:
        request: FastAPI request object

    Returns:
        FirebaseUser with verified identity

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
        logger.info(f"FIREBASE_AUTH ALLOW path={path} uid={user.uid} email={user.email}")
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
