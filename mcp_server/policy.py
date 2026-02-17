"""
Authorization policy for Health Advisor MCP Server.
Enforces tool-level access control based on user-patient mappings.
"""

import logging
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


def get_authz_config() -> dict:
    """Get the authz section of config."""
    config = load_config()
    return config.get("authz", {})


def is_authz_enabled() -> bool:
    """Check if authorization is enabled."""
    return get_authz_config().get("enabled", False)


def get_allowed_patient_id(sub: str) -> Optional[int]:
    """
    Get the patient ID that a user (sub) is allowed to access.

    Args:
        sub: Subject identifier (user ID)

    Returns:
        Patient ID if mapping exists, None otherwise (deny by default)
    """
    authz_config = get_authz_config()
    user_patient_map = authz_config.get("user_patient_map", {})
    patient_id = user_patient_map.get(sub)

    # Ensure it's an int if present
    if patient_id is not None:
        return int(patient_id)
    return None


def authorize_patient_access(sub: str, patient_id: int) -> bool:
    """
    Check if a user is authorized to access a specific patient's data.

    Args:
        sub: Subject identifier (user ID)
        patient_id: Patient ID being accessed

    Returns:
        True if authorized, False otherwise
    """
    allowed_patient_id = get_allowed_patient_id(sub)

    # Deny if no mapping exists for this user
    if allowed_patient_id is None:
        return False

    # Check if the requested patient matches the allowed patient
    return allowed_patient_id == patient_id


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
