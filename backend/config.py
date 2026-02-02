"""
Configuration loader for Health Advisor backend.
"""

import os
from pathlib import Path
from typing import Any

import yaml


def load_config() -> dict[str, Any]:
    """Load configuration from config.yaml."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


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
