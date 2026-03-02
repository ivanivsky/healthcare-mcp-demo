"""
Authorization policy for Health Advisor MCP Server.

Enforces tool-level access control based on claims from the auth_context.
Authorization is derived entirely from the verified JWT claims sent by the backend.
No external lookup (YAML, database) is performed.

Includes security controls for MCP transport authentication and auth_context
signature verification.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("mcp_policy")

# Cached config
_config: dict | None = None


def load_config() -> dict:
    """Load configuration from config.yaml."""
    global _config
    if _config is None:
        config_path = Path(__file__).parent.parent / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                _config = yaml.safe_load(f) or {}
        else:
            _config = {}
    return _config


def get_security_config() -> dict:
    """Get the security section of config."""
    config = load_config()
    return config.get("security", {})


def get_authz_config() -> dict:
    """Get the authz section of config."""
    config = load_config()
    return config.get("authz", {})


def is_authz_enabled() -> bool:
    """
    Check if authorization is enabled.

    Now checks the security.authorization_required control first,
    falling back to authz.enabled for backwards compatibility.
    """
    security = get_security_config()
    if "authorization_required" in security:
        return security.get("authorization_required", True)
    # Fallback to legacy authz.enabled
    return get_authz_config().get("enabled", False)


# ============================================================================
# MCP Transport Security Controls
# ============================================================================

def is_mcp_transport_auth_required() -> bool:
    """
    Check if MCP transport bearer token is required.

    When true, the MCP server requires a valid bearer token in the
    Authorization header for SSE connections.
    """
    return get_security_config().get("mcp_transport_auth_required", True)


def is_auth_context_signing_required() -> bool:
    """
    Check if auth_context JWT signature verification is required.

    When true, auth_context must contain a signed JWT token.
    When false, auth_context is trusted as-is (insecure).
    """
    return get_security_config().get("mcp_auth_context_signing_required", True)


def extract_auth_context(raw_auth_context: dict | None) -> dict | None:
    """
    Extract and verify auth_context from the raw dict passed by the backend.

    When signing is required:
      - raw_auth_context must contain a "token" key with a signed JWT
      - Verifies signature and expiry using MCP_JWT_SECRET
      - Returns the decoded claims dict on success
      - Returns None if verification fails (caller should deny)

    When signing is not required:
      - raw_auth_context is used as-is (plain dict with sub, role, etc.)
      - Logs a warning that unverified claims are being trusted

    Args:
        raw_auth_context: The auth_context dict from the tool call arguments

    Returns:
        The normalized auth_context dict with sub, role, patient_ids, etc.
        Returns None if verification fails or no context provided.
    """
    if not raw_auth_context:
        return None

    if is_auth_context_signing_required():
        token = raw_auth_context.get("token")
        if not token:
            logger.warning(
                "AUTH_CONTEXT_VERIFY failed: "
                "signing required but no token present"
            )
            return None

        secret = os.environ.get("MCP_JWT_SECRET")
        if not secret:
            logger.error(
                "MCP_JWT_SECRET not set but "
                "mcp_auth_context_signing_required is enabled"
            )
            return None

        try:
            import jwt
            claims = jwt.decode(token, secret, algorithms=["HS256"])
            logger.debug(
                f"AUTH_CONTEXT_VERIFY success sub={claims.get('sub')} "
                f"request_id={claims.get('request_id')}"
            )
            return claims
        except jwt.ExpiredSignatureError:
            logger.warning("AUTH_CONTEXT_VERIFY failed: token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"AUTH_CONTEXT_VERIFY failed: {e}")
            return None
    else:
        # Signing disabled — trust the raw context as-is
        logger.warning(
            "AUTH_CONTEXT_SIGNING DISABLED — trusting unverified claims. "
            f"sub={raw_auth_context.get('sub')} "
            "This is an insecure configuration."
        )
        return raw_auth_context


def authorize_patient_access(
    patient_id: int,
    auth_context: dict,
) -> bool:
    """
    Check if the caller is authorized to access a specific patient.

    Authorization is derived from the claims embedded in auth_context —
    the same claims that were verified by Firebase on the backend.
    No external lookup is performed.

    Args:
        patient_id: The patient being accessed
        auth_context: Dict containing role, patient_ids, sub, request_id

    Returns:
        True if authorized, False otherwise
    """
    role = auth_context.get("role", "patient")
    patient_ids = auth_context.get("patient_ids", [])

    # Admins can access any patient
    if role == "admin":
        return True

    # All other roles: check the patient_ids list from claims
    return patient_id in patient_ids


def log_authz_decision(
    tool: str,
    patient_id: int | None,
    sub: str | None,
    request_id: str | None,
    decision: str,
    reason: str = "",
) -> None:
    """
    Log an authorization decision for audit purposes.

    Args:
        tool: Tool name being called
        patient_id: Patient ID being accessed
        sub: Subject identifier
        request_id: Request trace ID
        decision: "allow" or "deny"
        reason: Optional reason for the decision
    """
    log_data = {
        "tool": tool,
        "patient_id": patient_id,
        "sub": sub,
        "request_id": request_id,
        "decision": decision,
    }
    if reason:
        log_data["reason"] = reason

    if decision == "allow":
        logger.info(f"Authz decision: {log_data}")
    else:
        logger.warning(f"Authz decision: {log_data}")
