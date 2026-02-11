"""
Configuration loader for Health Advisor backend.
Supports environment variable overrides for Cloud Run deployment.
"""

import os
from pathlib import Path
from typing import Any

import yaml


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

    return config


def get_config() -> dict[str, Any]:
    """Get cached configuration."""
    if not hasattr(get_config, "_cache"):
        get_config._cache = load_config()
    return get_config._cache


def get_anthropic_api_key() -> str:
    """Get Anthropic API key from environment."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Please set it before running the application."
        )
    return api_key
