"""
Standalone test script for the Appointments MCP Server.

Connects directly to the appointments MCP server on port 8002 and
exercises each tool. Bypasses the backend and web app — talks to
the MCP server over SSE transport.

Usage:
    python scripts/test_appointments_mcp.py

Prerequisites:
    - Cloud SQL proxy running (or DB accessible)
    - Appointments MCP server running:
        python -m mcp_server.appointments_server
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import jwt
from dotenv import load_dotenv
load_dotenv()

from mcp import ClientSession
from mcp.client.sse import sse_client

SERVER_URL = "http://localhost:8002/sse"

# Read bearer token from .env — same token the backend uses for the health MCP server
_token = os.environ.get("MCP_INTERNAL_TOKEN")
if not _token:
    print("WARNING: MCP_INTERNAL_TOKEN not set in .env — connection will likely get 401")
HEADERS = {"Authorization": f"Bearer {_token}"} if _token else {}

_jwt_secret = os.environ.get("MCP_JWT_SECRET")
if not _jwt_secret:
    print("WARNING: MCP_JWT_SECRET not set in .env — auth_context signing will fail")


def _make_auth(role: str, patient_ids: list, sub: str) -> dict:
    """
    Build a signed auth_context dict the same way mcp_client.py does.
    Encodes claims as a short-lived HS256 JWT and wraps it in {"token": ...}.
    """
    if not _jwt_secret:
        raise RuntimeError("MCP_JWT_SECRET must be set in .env to run this test")
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": sub,
            "role": role,
            "patient_ids": patient_ids,
            "actor_type": "user",
            "request_id": f"test-{now}",
            "iat": now,
            "exp": now + 60,
        },
        _jwt_secret,
        algorithm="HS256",
    )
    return {"token": token}


# Auth contexts are built fresh at call time so the JWT exp is never stale.
def ADMIN_AUTH() -> dict:
    return _make_auth("admin", [], "test-script")

def PATIENT1_AUTH() -> dict:
    return _make_auth("patient", [1], "test-patient-1")


def _parse(result, label: str = "") -> object:
    """
    Extract, print, and parse the first content item from a tool result.

    Prints the raw text so structure mismatches are immediately visible,
    then tries JSON decoding. Returns the decoded object, or the raw text
    string if JSON decoding fails.
    """
    if not result.content:
        print(f"  [{label}] raw response: <empty content>")
        return {}

    item = result.content[0]

    # TextContent has .text; other types (image, resource) may not
    raw = getattr(item, "text", None)
    if raw is None:
        print(f"  [{label}] raw response: <non-text content type={type(item).__name__}>")
        return {}

    print(f"  [{label}] raw: {raw[:400]}{'...' if len(raw) > 400 else ''}")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


async def test():
    print(f"Connecting to Appointments MCP server at {SERVER_URL}")
    print("=" * 60)

    async with sse_client(SERVER_URL, headers=HEADERS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ── List tools ────────────────────────────────────────────────
            tools_response = await session.list_tools()
            tool_names = [t.name for t in tools_response.tools]
            print(f"Available tools ({len(tool_names)}): {tool_names}")
            assert len(tool_names) == 5, f"Expected 5 tools, got {len(tool_names)}"
            print()

            # ── list_providers ────────────────────────────────────────────
            print("TEST: list_providers(accepting_only=False)")
            result = await session.call_tool(
                "list_providers",
                {"accepting_only": False, "auth_context": ADMIN_AUTH()},
            )
            data = _parse(result, "list_providers")
            providers = data.get("providers", []) if isinstance(data, dict) else []
            count = data.get("count", len(providers)) if isinstance(data, dict) else 0
            print(f"  providers count: {count}")
            for p in providers:
                flag = "✓" if p.get("accepting_new_patients") else "✗"
                print(f"    [{flag}] {p.get('name')} — {p.get('specialty')}")
            assert count == 5, f"Expected 5 providers, got {count}"
            print()

            print("TEST: list_providers(accepting_only=True)")
            result = await session.call_tool(
                "list_providers",
                {"accepting_only": True, "auth_context": ADMIN_AUTH()},
            )
            data = _parse(result, "list_providers accepting_only")
            providers_accepting = data.get("providers", []) if isinstance(data, dict) else []
            count_accepting = data.get("count", len(providers_accepting)) if isinstance(data, dict) else 0
            print(f"  accepting-only count: {count_accepting}")
            assert count_accepting == 4, f"Expected 4 accepting providers, got {count_accepting}"
            print()

            # ── get_available_appointments ────────────────────────────────
            print("TEST: get_available_appointments(date_from=2026-05-01, date_to=2026-05-07)")
            result = await session.call_tool(
                "get_available_appointments",
                {
                    "date_from": "2026-05-01",
                    "date_to": "2026-05-07",
                    "auth_context": ADMIN_AUTH(),
                },
            )
            data = _parse(result, "get_available_appointments")
            slots = data.get("slots", []) if isinstance(data, dict) else []
            print(f"  slots in first week: {len(slots)}")
            if slots:
                s = slots[0]
                print(f"  first slot: {s.get('provider_name')} on {s.get('slot_date')} at {s.get('slot_time')} (id={s.get('id')})")
            print()

            # ── get_my_appointments ───────────────────────────────────────
            print("TEST: get_my_appointments(patient_id=1, upcoming_only=False)")
            result = await session.call_tool(
                "get_my_appointments",
                {
                    "patient_id": 1,
                    "upcoming_only": False,
                    "auth_context": ADMIN_AUTH(),
                },
            )
            data = _parse(result, "get_my_appointments")
            appointments = data.get("appointments", []) if isinstance(data, dict) else []
            print(f"  appointments for patient 1: {len(appointments)}")
            if appointments:
                a = appointments[0]
                print(f"  first: {a.get('provider_name')} on {a.get('slot_date')} at {a.get('slot_time')} ({a.get('status')})")
            print()

            # ── BOLA enforcement test ─────────────────────────────────────
            print("TEST: BOLA — patient_id=2 with patient_ids=[1] (should be denied)")
            result = await session.call_tool(
                "get_my_appointments",
                {
                    "patient_id": 2,
                    "auth_context": PATIENT1_AUTH(),  # only authorized for patient 1
                },
            )
            data = _parse(result, "BOLA check")
            assert isinstance(data, dict) and data.get("error") == "forbidden", (
                f"Expected forbidden error, got: {data}"
            )
            print("  ✓ BOLA correctly blocked cross-patient access")
            print()

            # ── book_appointment ──────────────────────────────────────────
            print("TEST: book_appointment — find a free slot for patient 1 and book it")
            result = await session.call_tool(
                "get_available_appointments",
                {
                    "date_from": "2026-05-05",
                    "date_to": "2026-05-05",
                    "auth_context": ADMIN_AUTH(),
                },
            )
            slots_data = _parse(result, "slots for 2026-05-05")
            available_slots = slots_data.get("slots", []) if isinstance(slots_data, dict) else []
            if available_slots:
                test_slot = available_slots[0]
                slot_id = test_slot.get("id")
                print(f"  booking slot {slot_id}: {test_slot.get('provider_name')} at {test_slot.get('slot_time')}")

                result = await session.call_tool(
                    "book_appointment",
                    {
                        "patient_id": 1,
                        "slot_id": slot_id,
                        "reason": "Test booking via test script",
                        "auth_context": ADMIN_AUTH(),
                    },
                )
                data = _parse(result, "book_appointment")

                if isinstance(data, dict) and data.get("success"):
                    appointment_id = data.get("appointment_id")
                    print(f"  ✓ booked — appointment_id={appointment_id}")

                    # Try booking same slot again — should fail
                    print("TEST: double-book same slot (should fail)")
                    result = await session.call_tool(
                        "book_appointment",
                        {
                            "patient_id": 1,
                            "slot_id": slot_id,
                            "reason": "Duplicate booking attempt",
                            "auth_context": ADMIN_AUTH(),
                        },
                    )
                    data2 = _parse(result, "double-book attempt")
                    assert isinstance(data2, dict) and "error" in data2, (
                        f"Expected error on double-book, got: {data2}"
                    )
                    print(f"  ✓ double-book correctly rejected: {data2.get('error')}")
                    print()

                    # ── cancel_appointment ────────────────────────────────
                    print(f"TEST: cancel_appointment(appointment_request_id={appointment_id})")
                    result = await session.call_tool(
                        "cancel_appointment",
                        {
                            "patient_id": 1,
                            "appointment_request_id": appointment_id,
                            "auth_context": ADMIN_AUTH(),
                        },
                    )
                    data = _parse(result, "cancel_appointment")
                    assert isinstance(data, dict) and data.get("success"), (
                        f"Expected success, got: {data}"
                    )
                    print("  ✓ cancelled successfully")
                    print()

                    # Verify slot is available again
                    result = await session.call_tool(
                        "get_available_appointments",
                        {
                            "date_from": "2026-05-05",
                            "date_to": "2026-05-05",
                            "auth_context": ADMIN_AUTH(),
                        },
                    )
                    restored = _parse(result, "slots after cancel")
                    restored_slots = restored.get("slots", []) if isinstance(restored, dict) else []
                    slot_ids = [s.get("id") for s in restored_slots]
                    assert slot_id in slot_ids, (
                        f"Expected slot {slot_id} to be available again after cancel"
                    )
                    print(f"  ✓ slot {slot_id} is available again after cancellation")
                else:
                    print(f"  note: booking returned error: {data.get('error') if isinstance(data, dict) else data}")
            else:
                print("  no available slots on 2026-05-05 to test booking")
            print()

            print("=" * 60)
            print("All tests passed.")


if __name__ == "__main__":
    asyncio.run(test())
