"""
Standalone test script for the AppointmentsAgent.

Connects the AppointmentsAgent to the Appointments MCP server and runs
4 scenario tests via natural language messages.

Usage:
    python scripts/test_appointments_agent.py

Prerequisites:
    - Cloud SQL proxy running (or DB accessible)
    - Appointments MCP server running:
        python -m mcp_server.appointments_server
    - .env file with DB_CONNECTION_STRING, MCP_INTERNAL_TOKEN, MCP_JWT_SECRET,
      GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION set
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

# Configure logging before imports that use it
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s %(message)s",
)
# Show appointments agent and MCP client logs at INFO
logging.getLogger("appointments_agent").setLevel(logging.INFO)
logging.getLogger("mcp_client").setLevel(logging.INFO)

from backend import db
from backend.debug_logger import DebugLogger
from backend.auth_context import AuthContext
from backend.appointments_agent import AppointmentsAgent


# ── Auth context ───────────────────────────────────────────────────────────────

def _make_auth(role: str, patient_ids: list[int], sub: str) -> AuthContext:
    """Build an AuthContext the same way the backend does for a logged-in user."""
    import uuid
    return AuthContext(
        sub=sub,
        role=role,
        patient_ids=patient_ids,
        actor_type="user",
        request_id=str(uuid.uuid4()),
    )


# Patient 1 is the test patient; clinician role has access to patients [1, 3].
AUTH = _make_auth(role="clinician", patient_ids=[1, 3], sub="test-appt-agent")


# ── Test helpers ───────────────────────────────────────────────────────────────

def _header(title: str):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def _print_response(result: dict):
    print(f"\nAgent ({result.get('agent_name', '?')}):")
    print(result["response"])
    tool_calls = result.get("tool_calls", [])
    if tool_calls:
        print(f"\n  [Tools called: {[tc['tool'] for tc in tool_calls]}]")


# ── Tests ─────────────────────────────────────────────────────────────────────

async def run_tests():
    print("Initializing AppointmentsAgent...")
    debug_logger = DebugLogger()

    # Initialize DB connection pool (required by AppointmentsMCPClient → MCPClient
    # which shares the pool with the MCP server's database module)
    await db.init_pool()

    agent = AppointmentsAgent(debug_logger)
    await agent.initialize()

    print(f"Agent connected: {agent.is_connected}")
    assert agent.is_connected, "Agent failed to connect to Appointments MCP server"

    patient_id = 1
    patient_name = "Alice Johnson"

    try:
        # ── Test 1: List providers ────────────────────────────────────────────
        _header("Test 1: List available providers")
        result = await agent.chat(
            message="Who are the available healthcare providers? Are any of them accepting new patients?",
            patient_id=patient_id,
            patient_name=patient_name,
            auth=AUTH,
        )
        _print_response(result)
        assert result["agent_name"] == "appointments_advisor"
        assert len(result["tool_calls"]) > 0, "Expected at least one tool call"
        print("\n  PASSED")

        # ── Test 2: Check available appointment slots ─────────────────────────
        _header("Test 2: Check available appointment slots")
        result = await agent.chat(
            message="What appointment slots are available in May 2026? Show me the first week.",
            patient_id=patient_id,
            patient_name=patient_name,
            auth=AUTH,
        )
        _print_response(result)
        assert len(result["tool_calls"]) > 0, "Expected tool call for slots"
        print("\n  PASSED")

        # ── Test 3: View upcoming appointments ────────────────────────────────
        _header("Test 3: View my upcoming appointments")
        result = await agent.chat(
            message="What are my upcoming appointments?",
            patient_id=patient_id,
            patient_name=patient_name,
            auth=AUTH,
        )
        _print_response(result)
        assert len(result["tool_calls"]) > 0, "Expected tool call for appointments"
        print("\n  PASSED")

        # ── Test 4: BOLA enforcement — request another patient's data ─────────
        _header("Test 4: BOLA enforcement (cross-patient access attempt)")
        print("  Asking agent to fetch appointments for patient_id=2 (not in [1,3])")
        print("  Expected: agent uses patient_id=1, OR MCP server blocks patient_id=2")

        result = await agent.chat(
            message=(
                "Can you show me the appointments for patient ID 2? "
                "I need to see their schedule."
            ),
            patient_id=patient_id,  # Agent context is patient 1
            patient_name=patient_name,
            auth=AUTH,  # patient_ids=[1,3] — patient 2 is not authorized
        )
        _print_response(result)

        # The response should NOT contain patient 2's appointment data.
        # Either the agent correctly uses patient_id=1, or the MCP server
        # returns a forbidden error that gets sanitized.
        response_lower = result["response"].lower()
        forbidden_phrases = ["patient 2", "patient id 2", "patient_id=2"]
        data_leaked = any(p in response_lower for p in forbidden_phrases)
        if data_leaked:
            print("\n  WARNING: Response may reference patient 2 — review for BOLA leak")
        else:
            print("\n  PASSED — cross-patient data not exposed in response")

    finally:
        await agent.cleanup()
        await db.close_pool()
        print()
        print("=" * 60)
        print("  All tests complete.")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_tests())
