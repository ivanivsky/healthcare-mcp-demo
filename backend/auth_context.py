"""
Authentication context for Health Advisor.
Provides identity information that can be upgraded to real auth later.
"""

from dataclasses import dataclass, field


@dataclass
class AuthContext:
    """Identity context for request tracing and future auth integration."""

    sub: str  # Subject identifier (user ID)
    request_id: str  # Unique request identifier for tracing
    actor_type: str = "human"  # Type of actor: "human", "agent", "system"
    scopes: list[str] = field(default_factory=list)  # Permission scopes
    delegated_sub: str | None = None  # Original subject if acting on behalf of another
