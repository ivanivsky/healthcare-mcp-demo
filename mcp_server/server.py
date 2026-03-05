"""
My Health Access MCP Server

Exposes patient health information (PHI) through MCP tools.
Supports configurable transports: stdio, sse, streamable-http

Run with: python mcp_server/server.py
"""

import asyncio
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
from mcp_server.database import (
    init_database,
    get_all_patients,
    get_patient_by_id,
    get_patient_conditions,
    get_patient_prescriptions,
    get_patient_appointments,
    get_patient_insurance,
    get_patient_lab_results,
)
from mcp_server.policy import (
    is_authz_enabled,
    authorize_patient_access,
    log_authz_decision,
    is_mcp_transport_auth_required,
    extract_auth_context,
)

# Load configuration
def load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}

config = load_config()

# Setup logging
log_level = config.get("logging", {}).get("level", "INFO")
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mcp_server")

# Create MCP server instance
mcp = FastMCP(
    "My Health Access MCP Server",
    version="1.0.0",
)


# ============================================================================
# Authorization Helper
# ============================================================================

def check_patient_authorization(
    tool_name: str,
    patient_id: int,
    raw_auth_context: dict | None,
) -> dict | None:
    """
    Check if the caller is authorized to access a patient's data.

    Args:
        tool_name: Name of the tool being called
        patient_id: Patient ID being accessed
        raw_auth_context: Raw auth context dict (may contain signed JWT token)

    Returns:
        None if authorized, error dict if denied
    """
    # Extract and verify auth_context (handles JWT verification if required)
    auth_context = extract_auth_context(raw_auth_context)

    # If signing is required but verification failed, deny
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
            "request_id": None,
        }

    # Skip authorization if disabled
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

    # Extract auth info
    sub = auth_context.get("sub") if auth_context else None
    request_id = auth_context.get("request_id") if auth_context else None

    # Deny if no auth context
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

    # Check patient access using claims from auth_context
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
            "message": f"User '{sub}' is not authorized to access patient {patient_id}",
            "request_id": request_id,
        }

    # Authorized
    log_authz_decision(
        tool=tool_name,
        patient_id=patient_id,
        sub=sub,
        request_id=request_id,
        decision="allow",
    )
    return None


# ============================================================================
# MCP Tools - Patient Health Information
# ============================================================================

@mcp.tool()
async def list_patients(auth_context: dict | None = None) -> dict:
    """
    List patients the authenticated user is authorized to access.
    Returns basic info for patient selection dropdown.

    Authorization is derived from auth_context claims:
    - admin role: returns all patients
    - other roles: returns only patients in patient_ids list
    """
    # Extract and verify auth_context (handles JWT verification if required)
    raw_auth_context = auth_context
    auth_context = extract_auth_context(raw_auth_context)

    # If signing is required but verification failed, return empty list
    if auth_context is None and raw_auth_context is not None:
        logger.warning("list_patients: auth_context verification failed")
        return {"patients": [], "count": 0, "error": "auth_context_verification_failed"}

    sub = auth_context.get("sub") if auth_context else None
    request_id = auth_context.get("request_id") if auth_context else None
    role = auth_context.get("role", "patient") if auth_context else "patient"
    patient_ids = auth_context.get("patient_ids", []) if auth_context else []

    logger.info(f"Tool called: list_patients (sub={sub}, role={role}, patient_ids={patient_ids}, request_id={request_id})")

    # If authz is disabled, return all patients
    if not is_authz_enabled():
        patients = await get_all_patients()
        log_authz_decision(
            tool="list_patients",
            patient_id=None,
            sub=sub,
            request_id=request_id,
            decision="allow",
            reason="authz_disabled",
        )
        return {
            "patients": patients,
            "count": len(patients)
        }

    # If no auth context or no sub, return empty list (don't leak patient metadata)
    if not auth_context or not sub:
        log_authz_decision(
            tool="list_patients",
            patient_id=None,
            sub=sub,
            request_id=request_id,
            decision="deny",
            reason="missing_auth_context",
        )
        return {
            "patients": [],
            "count": 0
        }

    # Admin role: return all patients
    if role == "admin":
        patients = await get_all_patients()
        log_authz_decision(
            tool="list_patients",
            patient_id=None,
            sub=sub,
            request_id=request_id,
            decision="allow",
            reason="admin_role",
        )
        return {
            "patients": patients,
            "count": len(patients)
        }

    # Non-admin: return only patients from claims
    if not patient_ids:
        log_authz_decision(
            tool="list_patients",
            patient_id=None,
            sub=sub,
            request_id=request_id,
            decision="allow",
            reason="no_patient_ids_in_claims",
        )
        return {
            "patients": [],
            "count": 0
        }

    # Fetch each authorized patient
    patients = []
    for pid in patient_ids:
        patient = await get_patient_by_id(pid)
        if patient:
            # Return only basic info needed for dropdown
            patients.append({
                "id": patient["id"],
                "first_name": patient["first_name"],
                "last_name": patient["last_name"],
                "member_id": patient["member_id"],
            })

    log_authz_decision(
        tool="list_patients",
        patient_id=None,
        sub=sub,
        request_id=request_id,
        decision="allow",
        reason=f"claims_based_access ({len(patients)} patients)",
    )
    return {
        "patients": patients,
        "count": len(patients)
    }


@mcp.tool()
async def get_patient_demographics(patient_id: int, auth_context: dict | None = None) -> dict:
    """
    Get demographic information for a specific patient.

    Args:
        patient_id: The unique identifier of the patient

    Returns:
        Patient demographics including name, DOB, address, contact info, member ID
    """
    logger.info(f"Tool called: get_patient_demographics(patient_id={patient_id})")

    # Check authorization
    authz_error = check_patient_authorization("get_patient_demographics", patient_id, auth_context)
    if authz_error:
        return authz_error

    patient = await get_patient_by_id(patient_id)
    if not patient:
        return {"error": f"Patient with ID {patient_id} not found"}
    return {"patient": patient}


@mcp.tool()
async def get_medical_records(patient_id: int, auth_context: dict | None = None) -> dict:
    """
    Get medical records and diagnoses for a patient.

    Args:
        patient_id: The unique identifier of the patient

    Returns:
        List of medical conditions, diagnoses, and their status
    """
    logger.info(f"Tool called: get_medical_records(patient_id={patient_id})")

    # Check authorization
    authz_error = check_patient_authorization("get_medical_records", patient_id, auth_context)
    if authz_error:
        return authz_error

    conditions = await get_patient_conditions(patient_id)
    return {
        "patient_id": patient_id,
        "conditions": conditions,
        "count": len(conditions)
    }


@mcp.tool()
async def get_prescriptions(patient_id: int, active_only: bool = True, auth_context: dict | None = None) -> dict:
    """
    Get prescription medications for a patient.

    Args:
        patient_id: The unique identifier of the patient
        active_only: If True, only return active prescriptions (default: True)

    Returns:
        List of prescriptions with medication details, dosage, and pharmacy info
    """
    logger.info(f"Tool called: get_prescriptions(patient_id={patient_id}, active_only={active_only})")

    # Check authorization
    authz_error = check_patient_authorization("get_prescriptions", patient_id, auth_context)
    if authz_error:
        return authz_error

    prescriptions = await get_patient_prescriptions(patient_id, active_only)
    return {
        "patient_id": patient_id,
        "prescriptions": prescriptions,
        "count": len(prescriptions),
        "active_only": active_only
    }


@mcp.tool()
async def get_appointments(patient_id: int, upcoming_only: bool = True, auth_context: dict | None = None) -> dict:
    """
    Get appointments for a patient.

    Args:
        patient_id: The unique identifier of the patient
        upcoming_only: If True, only return future scheduled appointments (default: True)

    Returns:
        List of appointments with provider, date, location, and reason
    """
    logger.info(f"Tool called: get_appointments(patient_id={patient_id}, upcoming_only={upcoming_only})")

    # Check authorization
    authz_error = check_patient_authorization("get_appointments", patient_id, auth_context)
    if authz_error:
        return authz_error

    appointments = await get_patient_appointments(patient_id, upcoming_only)
    return {
        "patient_id": patient_id,
        "appointments": appointments,
        "count": len(appointments),
        "upcoming_only": upcoming_only
    }


@mcp.tool()
async def get_insurance_info(patient_id: int, auth_context: dict | None = None) -> dict:
    """
    Get insurance information for a patient.

    Args:
        patient_id: The unique identifier of the patient

    Returns:
        Insurance details including provider, plan, policy numbers, and coverage info
    """
    logger.info(f"Tool called: get_insurance_info(patient_id={patient_id})")

    # Check authorization
    authz_error = check_patient_authorization("get_insurance_info", patient_id, auth_context)
    if authz_error:
        return authz_error

    insurance = await get_patient_insurance(patient_id)
    return {
        "patient_id": patient_id,
        "insurance_plans": insurance,
        "count": len(insurance)
    }


@mcp.tool()
async def get_lab_results(patient_id: int, limit: int = 20, auth_context: dict | None = None) -> dict:
    """
    Get laboratory test results for a patient.

    Args:
        patient_id: The unique identifier of the patient
        limit: Maximum number of results to return (default: 20)

    Returns:
        List of lab results with test names, values, reference ranges, and status
    """
    logger.info(f"Tool called: get_lab_results(patient_id={patient_id}, limit={limit})")

    # Check authorization
    authz_error = check_patient_authorization("get_lab_results", patient_id, auth_context)
    if authz_error:
        return authz_error

    results = await get_patient_lab_results(patient_id, limit)
    return {
        "patient_id": patient_id,
        "lab_results": results,
        "count": len(results)
    }


# ============================================================================
# Server Entry Point
# ============================================================================

async def init():
    """Initialize PostgreSQL connection pool before starting server."""
    logger.info("Initializing PostgreSQL connection pool...")
    await db.init_pool()
    logger.info("PostgreSQL connection pool ready")


async def shutdown():
    """Close PostgreSQL connection pool on shutdown."""
    logger.info("Closing PostgreSQL connection pool...")
    await db.close_pool()
    logger.info("PostgreSQL connection pool closed")


def run_sse_server(host: str, port: int):
    """Run MCP server with SSE transport using Starlette and SseServerTransport."""
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.responses import Response, JSONResponse
    from mcp.server.sse import SseServerTransport

    # Create SSE transport - the endpoint is where clients POST messages
    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        """Handle SSE connection requests with optional bearer token verification."""
        # Verify bearer token if transport auth is required
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
                    "MCP_TRANSPORT_AUTH DENY "
                    "reason=invalid_or_missing_bearer_token"
                )
                return Response(status_code=401)

            logger.debug("MCP_TRANSPORT_AUTH success")
        else:
            logger.warning(
                "MCP_TRANSPORT_AUTH DISABLED — accepting connection without "
                "bearer token verification. This is an insecure configuration."
            )

        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0],
                streams[1],
                mcp._mcp_server.create_initialization_options()
            )
        return Response()

    async def health_check(request):
        return JSONResponse({"status": "healthy", "server": "mcp"})

    # Create Starlette app with SSE routes
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

    logger.info(f"SSE server listening on http://{host}:{port}")
    logger.info(f"SSE endpoint: http://{host}:{port}/sse")
    logger.info(f"Messages endpoint: http://{host}:{port}/messages/")
    uvicorn.run(app, host=host, port=port, log_level="info")


def run_http_server(host: str, port: int):
    """Run MCP server with Streamable HTTP transport."""
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.responses import JSONResponse
    from mcp.server.streamable_http import StreamableHTTPServerTransport

    # Create HTTP transport
    http_transport = StreamableHTTPServerTransport("/messages/")

    async def handle_http(request):
        """Handle HTTP requests."""
        await http_transport.handle_request(
            request.scope, request.receive, request._send,
            mcp._mcp_server, mcp._mcp_server.create_initialization_options()
        )

    async def health_check(request):
        return JSONResponse({"status": "healthy", "server": "mcp"})

    # Create Starlette app
    app = Starlette(
        debug=True,
        routes=[
            Route("/health", health_check),
            Route("/mcp", endpoint=handle_http, methods=["POST"]),
        ],
        on_startup=[init],
        on_shutdown=[shutdown],
    )

    logger.info(f"HTTP server listening on http://{host}:{port}")
    logger.info(f"MCP endpoint: http://{host}:{port}/mcp")
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    """Run the MCP server with configured transport."""
    # Get transport configuration with environment variable overrides
    transport = os.environ.get("MCP_TRANSPORT", config.get("mcp", {}).get("transport", "stdio"))
    host = os.environ.get("MCP_HOST", config.get("mcp", {}).get("host", "localhost"))
    port = int(os.environ.get("MCP_PORT", config.get("mcp", {}).get("port", 8001)))

    logger.info(f"Starting MCP server with transport: {transport}")

    if transport == "stdio":
        # Initialize database first for stdio
        asyncio.run(init())
        # Standard input/output transport
        mcp.run(transport="stdio")
    elif transport == "sse":
        # Server-Sent Events transport with custom host/port
        run_sse_server(host, port)
    elif transport == "streamable-http" or transport == "http":
        # Streamable HTTP transport with custom host/port
        run_http_server(host, port)
    else:
        logger.error(f"Unknown transport: {transport}")
        sys.exit(1)


if __name__ == "__main__":
    main()
