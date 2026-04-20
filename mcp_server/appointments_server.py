"""
My Health Access Appointments MCP Server

Exposes appointment scheduling tools exclusively. Runs on port 8002.
Connects to the same PostgreSQL database as the health MCP server but
queries only the scheduling tables (providers, available_slots,
appointment_requests).

Health data tables (medical_records, prescriptions, lab_results,
insurance) are NOT accessible through any tool in this server.
This separation is the foundation of the tool scope enforcement demo.

Run with:
    python -m mcp_server.appointments_server
    python mcp_server/appointments_server.py
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import yaml
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
load_dotenv()

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import db
from mcp_server.appointments_database import (
    get_providers,
    get_available_slots,
    get_patient_appointments,
    book_appointment as db_book_appointment,
    cancel_appointment as db_cancel_appointment,
)
# Reuse all auth logic from the shared policy module — not duplicated here
from mcp_server.policy import (
    is_authz_enabled,
    is_mcp_transport_auth_required,
    authorize_patient_access,
    extract_auth_context,
    log_authz_decision,
)

# ── Config ────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}

config = _load_config()

# ── Logging ───────────────────────────────────────────────────────────────────

log_level = config.get("logging", {}).get("level", "INFO")
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("appointments_mcp_server")

# ── MCP server instance ───────────────────────────────────────────────────────

mcp = FastMCP("My Health Access Appointments MCP Server")


# ============================================================================
# Authorization helper
# ============================================================================

def _check_auth(
    tool_name: str,
    patient_id: int | None,
    raw_auth_context: dict | None,
) -> dict | None:
    """
    Validate auth_context and, for patient-scoped calls, enforce BOLA.

    All real auth logic lives in mcp_server.policy — this is just wiring.

    Args:
        tool_name: Tool name for audit logging.
        patient_id: Patient being accessed, or None for non-patient tools.
        raw_auth_context: Raw auth_context dict from the tool call.

    Returns:
        None if authorized.
        Error dict if denied — caller must return this to the agent.
    """
    # Decode / verify the auth context (JWT or plain dict per policy setting)
    auth_context = extract_auth_context(raw_auth_context)

    # Signing required but verification failed
    if auth_context is None and raw_auth_context is not None:
        log_authz_decision(
            tool=tool_name,
            patient_id=patient_id,
            sub=None,
            request_id=None,
            decision="deny",
            reason="auth_context_verification_failed",
        )
        return {
            "error": "forbidden",
            "message": "Invalid or missing auth context signature",
        }

    # For non-patient tools: just require a valid auth context
    if patient_id is None:
        if not is_authz_enabled():
            return None
        if not auth_context or not auth_context.get("sub"):
            log_authz_decision(
                tool=tool_name,
                patient_id=None,
                sub=None,
                request_id=None,
                decision="deny",
                reason="missing_auth_context",
            )
            return {"error": "forbidden", "message": "Authentication required"}
        return None

    # Authorization disabled globally — allow all patient access
    if not is_authz_enabled():
        log_authz_decision(
            tool=tool_name,
            patient_id=patient_id,
            sub=auth_context.get("sub") if auth_context else None,
            request_id=auth_context.get("request_id") if auth_context else None,
            decision="allow",
            reason="authz_disabled",
        )
        return None

    sub = auth_context.get("sub") if auth_context else None
    request_id = auth_context.get("request_id") if auth_context else None

    # No auth context at all
    if not auth_context or not sub:
        log_authz_decision(
            tool=tool_name,
            patient_id=patient_id,
            sub=sub,
            request_id=request_id,
            decision="deny",
            reason="missing_auth_context",
        )
        return {
            "error": "forbidden",
            "message": "Authentication required",
            "request_id": request_id,
        }

    # BOLA check — patient_id must be in caller's authorized claims
    if not authorize_patient_access(patient_id, auth_context):
        log_authz_decision(
            tool=tool_name,
            patient_id=patient_id,
            sub=sub,
            request_id=request_id,
            decision="deny",
            reason="patient_access_denied",
        )
        return {
            "error": "forbidden",
            "message": (
                f"User '{sub}' is not authorized to access patient {patient_id}"
            ),
            "request_id": request_id,
        }

    log_authz_decision(
        tool=tool_name,
        patient_id=patient_id,
        sub=sub,
        request_id=request_id,
        decision="allow",
    )
    return None


# ============================================================================
# MCP Tools — Appointments scheduling
# ============================================================================

@mcp.tool()
async def list_providers(
    accepting_only: bool = False,
    auth_context: dict | None = None,
) -> str:
    """
    List available healthcare providers.

    Args:
        accepting_only: If True, only return providers currently accepting
                        new patients.

    Returns list of providers with specialty, location, and availability
    status. Authentication required. No patient-specific data returned.
    """
    logger.info(f"Tool called: list_providers(accepting_only={accepting_only})")

    err = _check_auth("list_providers", None, auth_context)
    if err:
        return json.dumps(err)

    providers = await get_providers(accepting_only=accepting_only)
    return json.dumps({
        "providers": providers,
        "count": len(providers),
        "accepting_only": accepting_only,
    })


@mcp.tool()
async def get_available_appointments(
    provider_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    auth_context: dict | None = None,
) -> str:
    """
    Get available appointment slots.

    Args:
        provider_id: Filter by specific provider (optional).
        date_from: Start date YYYY-MM-DD (optional).
        date_to: End date YYYY-MM-DD (optional).

    Returns available slots with provider name, date, time, and the slot_id
    needed for booking. Authentication required.
    """
    logger.info(
        f"Tool called: get_available_appointments("
        f"provider_id={provider_id}, date_from={date_from}, date_to={date_to})"
    )

    err = _check_auth("get_available_appointments", None, auth_context)
    if err:
        return json.dumps(err)

    slots = await get_available_slots(
        provider_id=provider_id,
        date_from=date_from,
        date_to=date_to,
    )
    return json.dumps({
        "slots": slots,
        "count": len(slots),
        "filters": {
            "provider_id": provider_id,
            "date_from": date_from,
            "date_to": date_to,
        },
    })


@mcp.tool()
async def get_my_appointments(
    patient_id: int,
    upcoming_only: bool = True,
    auth_context: dict | None = None,
) -> str:
    """
    Get appointments for the authenticated patient.

    Args:
        patient_id: Patient ID — must match the caller's authorized patient_ids.
        upcoming_only: Only return future appointments (default True).

    Authorization: patient_id must be in the caller's authorized patient_ids
    claim, or caller must have admin role. Same BOLA enforcement as health tools.

    Returns list of appointments with provider, date, time, reason, and status.
    """
    logger.info(
        f"Tool called: get_my_appointments("
        f"patient_id={patient_id}, upcoming_only={upcoming_only})"
    )

    err = _check_auth("get_my_appointments", patient_id, auth_context)
    if err:
        return json.dumps(err)

    appointments = await get_patient_appointments(
        patient_id=patient_id,
        upcoming_only=upcoming_only,
    )
    return json.dumps({
        "patient_id": patient_id,
        "appointments": appointments,
        "count": len(appointments),
        "upcoming_only": upcoming_only,
    })


@mcp.tool()
async def book_appointment(
    patient_id: int,
    slot_id: int,
    reason: str,
    notes: str | None = None,
    auth_context: dict | None = None,
) -> str:
    """
    Book an appointment slot for the authenticated patient.

    Args:
        patient_id: Patient ID — must match the caller's authorized patient_ids.
        slot_id: ID of the available slot to book (from get_available_appointments).
        reason: Reason for the appointment.
        notes: Optional additional notes.

    Authorization: Same BOLA enforcement — patient_id must be in the caller's
    authorized patient_ids or caller must have admin role.

    Returns confirmation with provider name, date, time, and appointment ID.
    Returns an error message if the slot is no longer available.
    """
    logger.info(
        f"Tool called: book_appointment("
        f"patient_id={patient_id}, slot_id={slot_id}, reason={reason!r})"
    )

    err = _check_auth("book_appointment", patient_id, auth_context)
    if err:
        return json.dumps(err)

    try:
        result = await db_book_appointment(
            patient_id=patient_id,
            slot_id=slot_id,
            reason=reason,
            notes=notes,
        )
        logger.info(
            f"APPOINTMENT_BOOKED patient_id={patient_id} slot_id={slot_id} "
            f"appointment_id={result['appointment_id']}"
        )
        return json.dumps(result)
    except ValueError as e:
        logger.warning(f"APPOINTMENT_BOOK_FAILED patient_id={patient_id} slot_id={slot_id} error={e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
async def cancel_appointment(
    patient_id: int,
    appointment_request_id: int,
    auth_context: dict | None = None,
) -> str:
    """
    Cancel a confirmed appointment.

    Args:
        patient_id: Patient ID — must match the caller's authorized patient_ids.
        appointment_request_id: ID of the appointment request to cancel.

    Authorization: Same BOLA enforcement — patient_id must be in the caller's
    authorized patient_ids or caller must have admin role.

    Returns confirmation of cancellation or an error message.
    """
    logger.info(
        f"Tool called: cancel_appointment("
        f"patient_id={patient_id}, appointment_request_id={appointment_request_id})"
    )

    err = _check_auth("cancel_appointment", patient_id, auth_context)
    if err:
        return json.dumps(err)

    try:
        result = await db_cancel_appointment(
            patient_id=patient_id,
            appointment_request_id=appointment_request_id,
        )
        logger.info(
            f"APPOINTMENT_CANCELLED patient_id={patient_id} "
            f"appointment_request_id={appointment_request_id}"
        )
        return json.dumps(result)
    except ValueError as e:
        logger.warning(
            f"APPOINTMENT_CANCEL_FAILED patient_id={patient_id} "
            f"appointment_request_id={appointment_request_id} error={e}"
        )
        return json.dumps({"error": str(e)})


# ============================================================================
# Server lifecycle
# ============================================================================

async def init():
    """Initialize PostgreSQL connection pool."""
    logger.info("APPOINTMENTS_MCP: Initializing PostgreSQL connection pool...")
    await db.init_pool()
    logger.info("APPOINTMENTS_MCP: PostgreSQL connection pool ready")
    tool_names = [
        "list_providers",
        "get_available_appointments",
        "get_my_appointments",
        "book_appointment",
        "cancel_appointment",
    ]
    logger.info(
        f"APPOINTMENTS_MCP_SERVER_STARTED port=8002 tools={len(tool_names)}"
    )


async def shutdown():
    """Close PostgreSQL connection pool."""
    logger.info("APPOINTMENTS_MCP: Closing PostgreSQL connection pool...")
    await db.close_pool()
    logger.info("APPOINTMENTS_MCP: PostgreSQL connection pool closed")


def run_sse_server(host: str, port: int) -> None:
    """Run the appointments MCP server with SSE transport."""
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.responses import Response, JSONResponse
    from mcp.server.sse import SseServerTransport

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        """Handle SSE connection with optional bearer token verification."""
        if is_mcp_transport_auth_required():
            expected = os.environ.get("MCP_INTERNAL_TOKEN")
            auth_header = request.headers.get("Authorization", "")

            if not expected:
                logger.error(
                    "MCP_INTERNAL_TOKEN not set but "
                    "mcp_transport_auth_required is enabled"
                )
                return Response(status_code=500)

            if auth_header != f"Bearer {expected}":
                logger.warning(
                    "APPOINTMENTS_MCP_TRANSPORT_AUTH DENY "
                    "reason=invalid_or_missing_bearer_token"
                )
                return Response(status_code=401)

            logger.debug("APPOINTMENTS_MCP_TRANSPORT_AUTH success")
        else:
            logger.warning(
                "APPOINTMENTS_MCP_TRANSPORT_AUTH DISABLED — "
                "accepting connection without bearer token verification."
            )

        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0],
                streams[1],
                mcp._mcp_server.create_initialization_options(),
            )
        return Response()

    async def health_check(request):
        return JSONResponse({"status": "healthy", "server": "appointments-mcp"})

    app = Starlette(
        debug=True,
        routes=[
            Route("/health", health_check),
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
        on_startup=[init],
        on_shutdown=[shutdown],
    )

    logger.info(f"Appointments MCP SSE server listening on http://{host}:{port}")
    logger.info(f"SSE endpoint:      http://{host}:{port}/sse")
    logger.info(f"Messages endpoint: http://{host}:{port}/messages/")
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    """Run the appointments MCP server."""
    appt_config = config.get("appointments_mcp", {})
    host = os.environ.get("APPOINTMENTS_MCP_HOST", "localhost")
    port = int(os.environ.get("APPOINTMENTS_MCP_PORT", 8002))
    transport = os.environ.get("MCP_TRANSPORT", "sse")

    logger.info(f"Starting Appointments MCP server transport={transport} port={port}")

    if transport == "sse":
        run_sse_server(host, port)
    else:
        logger.error(f"Unsupported transport for appointments server: {transport}")
        sys.exit(1)


if __name__ == "__main__":
    main()
