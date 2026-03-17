"""
Configuration loader for Health Advisor backend.
Supports environment variable overrides for Cloud Run deployment.
Includes runtime-mutable security controls.
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from dotenv import load_dotenv
load_dotenv()


logger = logging.getLogger("config")

# Module-level state for runtime security overrides
_runtime_security_overrides: dict = {}


def load_config() -> dict[str, Any]:
    """Load configuration from config.yaml with environment variable overrides."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f)
    else:
        config = {}

    # Apply environment variable overrides for Cloud Run
    if "mcp" not in config:
        config["mcp"] = {}
    if "backend" not in config:
        config["backend"] = {}

    # MCP server configuration overrides
    if os.environ.get("MCP_HOST"):
        config["mcp"]["host"] = os.environ["MCP_HOST"]
    if os.environ.get("MCP_PORT"):
        config["mcp"]["port"] = int(os.environ["MCP_PORT"])
    if os.environ.get("MCP_SERVER_URL"):
        config["mcp"]["server_url"] = os.environ["MCP_SERVER_URL"]

    # Backend configuration overrides
    if os.environ.get("BACKEND_HOST"):
        config["backend"]["host"] = os.environ["BACKEND_HOST"]
    if os.environ.get("BACKEND_PORT"):
        config["backend"]["port"] = int(os.environ["BACKEND_PORT"])

    # CORS configuration overrides
    if "cors" not in config:
        config["cors"] = {}
    if os.environ.get("CORS_ORIGINS"):
        # Comma-separated list of allowed origins
        config["cors"]["allowed_origins"] = [
            origin.strip()
            for origin in os.environ["CORS_ORIGINS"].split(",")
            if origin.strip()
        ]

    return config


def get_config() -> dict[str, Any]:
    """Get cached configuration."""
    if not hasattr(get_config, "_cache"):
        get_config._cache = load_config()
    return get_config._cache


# ============================================================================
# Runtime Security Configuration
# ============================================================================

def get_security_config() -> dict:
    """
    Get the current runtime security configuration.

    Returns the in-memory security overrides merged over the YAML defaults.
    """
    config = get_config()
    yaml_security = config.get("security", {})

    # Deep copy to avoid mutating the cached config
    merged = dict(yaml_security)

    # Merge runtime overrides (they take precedence)
    merged.update(_runtime_security_overrides)

    return merged


def update_security_config(updates: dict) -> dict:
    """
    Apply runtime updates to the security configuration.

    Updates are merged over the current config (not a full replacement).
    Changes are in-memory only — they reset to YAML defaults on restart.

    Args:
        updates: Dict of security control names to new values

    Returns:
        The full updated security config
    """
    global _runtime_security_overrides

    # Only update known security control keys (not nested objects like rate_limiting)
    # Note: system_prompt_level is a string, not a bool, but can still be updated
    valid_controls = {
        "authentication_required",
        "authorization_required",
        "mcp_transport_auth_required",
        "mcp_auth_context_signing_required",
        "prompt_injection_protection",
        "system_prompt_level",
        "deterministic_error_responses",
    }

    for key, value in updates.items():
        if key in valid_controls:
            _runtime_security_overrides[key] = value
            logger.info(f"SECURITY_OVERRIDE_SET control={key} value={value}")

    return get_security_config()


def reset_security_config() -> dict:
    """
    Reset security config to YAML defaults.

    Clears all runtime overrides.

    Returns:
        The reset config (YAML defaults)
    """
    global _runtime_security_overrides

    _runtime_security_overrides = {}
    logger.info("SECURITY_CONFIG_RESET cleared all runtime overrides")

    return get_security_config()


def is_security_control_enabled(control: str) -> bool:
    """
    Check if a named security control is enabled.

    Args:
        control: One of the keys in the security section of config.yaml
                 e.g., "authentication_required", "authorization_required"

    Returns:
        True if enabled, False if disabled or not found
    """
    security_config = get_security_config()
    return security_config.get(control, False)


def get_system_prompt_level() -> str:
    """
    Get the current system prompt security level.

    Returns:
        "insecure" | "weak" | "strong"
        Defaults to "strong" if not set or invalid.
    """
    security_config = get_security_config()
    level = security_config.get("system_prompt_level", "strong")
    if level not in ("insecure", "weak", "strong"):
        logger.warning(f"Invalid system_prompt_level '{level}', defaulting to 'strong'")
        return "strong"
    return level
