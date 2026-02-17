"""
Authentication utilities for Health Advisor.
Currently provides demo/stub auth; can be upgraded to real OAuth/OIDC later.
"""

import uuid

from fastapi import Request

from backend.auth_context import AuthContext


def get_auth_context(request: Request) -> AuthContext:
    """
    Extract or generate authentication context from a request.

    Currently uses demo headers; can be upgraded to real auth later.

    Headers:
        X-Demo-User: Override the subject identifier (default: "demo_user")
        X-Request-ID: Reuse an existing request ID (default: generate uuid4)
    """
    # Get subject from header or use default
    sub = request.headers.get("X-Demo-User", "demo_user")

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
