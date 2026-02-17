"""
Authentication utilities for My Health Access.
Supports session-based auth with header fallback for testing.
"""

import uuid

from fastapi import Request

from backend.auth_context import AuthContext


def get_auth_context(request: Request) -> AuthContext:
    """
    Extract or generate authentication context from a request.

    Identity priority:
        1) Session sub (request.session["sub"]) if present
        2) X-Demo-User header (for debugging/testing)
        3) Fallback to "demo_user"

    Headers:
        X-Demo-User: Override the subject identifier (for testing)
        X-Request-ID: Reuse an existing request ID (default: generate uuid4)
    """
    # Priority 1: Session-based identity
    sub = None
    session = getattr(request, "session", None)
    if session:
        sub = session.get("sub")

    # Priority 2: Header override (for testing)
    if not sub:
        sub = request.headers.get("X-Demo-User")

    # Priority 3: Default fallback
    if not sub:
        sub = "demo_user"

    # Get or generate request ID
    request_id = request.headers.get("X-Request-ID")
    if not request_id:
        request_id = str(uuid.uuid4())

    return AuthContext(
        sub=sub,
        request_id=request_id,
        actor_type="human",
        scopes=[],
        delegated_sub=None,
    )


# Valid usernames for login
VALID_USERS = {"alice", "bob"}
