"""
Health Advisor MCP Server

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

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

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
    "Health Advisor MCP Server",
    version="1.0.0",
)


# ============================================================================
# MCP Tools - Patient Health Information
# ============================================================================

@mcp.tool()
async def list_patients() -> dict:
    """
    List all patients in the system.
    Returns basic info for patient selection dropdown.
    """
    logger.info("Tool called: list_patients")
    patients = await get_all_patients()
    logger.debug(f"Found {len(patients)} patients")
    return {
        "patients": patients,
        "count": len(patients)
    }


@mcp.tool()
async def get_patient_demographics(patient_id: int) -> dict:
    """
    Get demographic information for a specific patient.

    Args:
        patient_id: The unique identifier of the patient

    Returns:
        Patient demographics including name, DOB, address, contact info, member ID
    """
    logger.info(f"Tool called: get_patient_demographics(patient_id={patient_id})")
    patient = await get_patient_by_id(patient_id)
    if not patient:
        return {"error": f"Patient with ID {patient_id} not found"}
    return {"patient": patient}


@mcp.tool()
async def get_medical_records(patient_id: int) -> dict:
    """
    Get medical records and diagnoses for a patient.

    Args:
        patient_id: The unique identifier of the patient

    Returns:
        List of medical conditions, diagnoses, and their status
    """
    logger.info(f"Tool called: get_medical_records(patient_id={patient_id})")
    conditions = await get_patient_conditions(patient_id)
    return {
        "patient_id": patient_id,
        "conditions": conditions,
        "count": len(conditions)
    }


@mcp.tool()
async def get_prescriptions(patient_id: int, active_only: bool = True) -> dict:
    """
    Get prescription medications for a patient.

    Args:
        patient_id: The unique identifier of the patient
        active_only: If True, only return active prescriptions (default: True)

    Returns:
        List of prescriptions with medication details, dosage, and pharmacy info
    """
    logger.info(f"Tool called: get_prescriptions(patient_id={patient_id}, active_only={active_only})")
    prescriptions = await get_patient_prescriptions(patient_id, active_only)
    return {
        "patient_id": patient_id,
        "prescriptions": prescriptions,
        "count": len(prescriptions),
        "active_only": active_only
    }


@mcp.tool()
async def get_appointments(patient_id: int, upcoming_only: bool = True) -> dict:
    """
    Get appointments for a patient.

    Args:
        patient_id: The unique identifier of the patient
        upcoming_only: If True, only return future scheduled appointments (default: True)

    Returns:
        List of appointments with provider, date, location, and reason
    """
    logger.info(f"Tool called: get_appointments(patient_id={patient_id}, upcoming_only={upcoming_only})")
    appointments = await get_patient_appointments(patient_id, upcoming_only)
    return {
        "patient_id": patient_id,
        "appointments": appointments,
        "count": len(appointments),
        "upcoming_only": upcoming_only
    }


@mcp.tool()
async def get_insurance_info(patient_id: int) -> dict:
    """
    Get insurance information for a patient.

    Args:
        patient_id: The unique identifier of the patient

    Returns:
        Insurance details including provider, plan, policy numbers, and coverage info
    """
    logger.info(f"Tool called: get_insurance_info(patient_id={patient_id})")
    insurance = await get_patient_insurance(patient_id)
    return {
        "patient_id": patient_id,
        "insurance_plans": insurance,
        "count": len(insurance)
    }


@mcp.tool()
async def get_lab_results(patient_id: int, limit: int = 20) -> dict:
    """
    Get laboratory test results for a patient.

    Args:
        patient_id: The unique identifier of the patient
        limit: Maximum number of results to return (default: 20)

    Returns:
        List of lab results with test names, values, reference ranges, and status
    """
    logger.info(f"Tool called: get_lab_results(patient_id={patient_id}, limit={limit})")
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
    """Initialize database before starting server."""
    logger.info("Initializing database...")
    await init_database()
    logger.info("Database initialized")


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
        """Handle SSE connection requests."""
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
    )

    logger.info(f"HTTP server listening on http://{host}:{port}")
    logger.info(f"MCP endpoint: http://{host}:{port}/mcp")
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    """Run the MCP server with configured transport."""
    # Get transport configuration
    transport = config.get("mcp", {}).get("transport", "stdio")
    host = config.get("mcp", {}).get("host", "localhost")
    port = config.get("mcp", {}).get("port", 8001)

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
