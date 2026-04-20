"""
Database module for the Appointments MCP Server.

Queries only the scheduling tables: providers, available_slots,
appointment_requests. Has no access to patient health data tables
(medical_records, prescriptions, lab_results, insurance).

Uses the shared PostgreSQL connection pool from backend.db.
"""

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# Add project root to path for backend module import
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import db

logger = logging.getLogger("appointments_db")


async def get_providers(accepting_only: bool = False) -> list[dict]:
    """
    Get list of healthcare providers.

    Args:
        accepting_only: If True, only return providers accepting new patients.

    Returns:
        List of providers with id, name, specialty, location, phone,
        accepting_new_patients.
    """
    if accepting_only:
        rows = await db.fetch(
            """SELECT id, name, specialty, location, phone, accepting_new_patients
               FROM providers
               WHERE accepting_new_patients = TRUE
               ORDER BY specialty, name"""
        )
    else:
        rows = await db.fetch(
            """SELECT id, name, specialty, location, phone, accepting_new_patients
               FROM providers
               ORDER BY specialty, name"""
        )
    return [dict(row) for row in rows]


async def get_available_slots(
    provider_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Get available appointment slots.

    Args:
        provider_id: Filter by provider (optional).
        date_from: Start date filter YYYY-MM-DD (optional).
        date_to: End date filter YYYY-MM-DD (optional).
        limit: Max results to return (default 20).

    Returns:
        Slots with id, provider_id, provider_name, slot_date, slot_time,
        duration_minutes, status. Only status='available' slots.
    """
    # Build dynamic WHERE clause
    conditions = ["s.status = 'available'"]
    params: list = []
    idx = 1

    if provider_id is not None:
        conditions.append(f"s.provider_id = ${idx}")
        params.append(provider_id)
        idx += 1

    if date_from:
        conditions.append(f"s.slot_date >= ${idx}")
        params.append(date.fromisoformat(date_from))
        idx += 1

    if date_to:
        conditions.append(f"s.slot_date <= ${idx}")
        params.append(date.fromisoformat(date_to))
        idx += 1

    params.append(limit)
    where = " AND ".join(conditions)

    query = f"""
        SELECT s.id,
               s.provider_id,
               p.name AS provider_name,
               s.slot_date::text AS slot_date,
               TO_CHAR(s.slot_time, 'HH24:MI') AS slot_time,
               s.duration_minutes,
               s.status
        FROM available_slots s
        JOIN providers p ON p.id = s.provider_id
        WHERE {where}
        ORDER BY s.slot_date, s.slot_time, p.name
        LIMIT ${idx}
    """

    rows = await db.fetch(query, *params)
    return [dict(row) for row in rows]


async def get_patient_appointments(
    patient_id: int,
    upcoming_only: bool = True,
) -> list[dict]:
    """
    Get appointment requests for a specific patient.

    Args:
        patient_id: The patient's ID.
        upcoming_only: If True, only return future appointments (default True).

    Returns:
        Appointment requests joined with providers and available_slots,
        including: id, provider_name, specialty, location, slot_date,
        slot_time, reason, status, notes.
    """
    date_filter = "AND s.slot_date >= CURRENT_DATE" if upcoming_only else ""

    query = f"""
        SELECT ar.id,
               p.name AS provider_name,
               p.specialty,
               p.location,
               s.slot_date::text AS slot_date,
               TO_CHAR(s.slot_time, 'HH24:MI') AS slot_time,
               ar.reason,
               ar.status,
               ar.notes,
               ar.requested_at::text AS requested_at
        FROM appointment_requests ar
        JOIN providers p ON p.id = ar.provider_id
        JOIN available_slots s ON s.id = ar.slot_id
        WHERE ar.patient_id = $1
          {date_filter}
        ORDER BY s.slot_date, s.slot_time
    """

    rows = await db.fetch(query, patient_id)
    return [dict(row) for row in rows]


async def book_appointment(
    patient_id: int,
    slot_id: int,
    reason: str,
    notes: str | None = None,
) -> dict:
    """
    Book an available slot for a patient.

    Steps:
    1. Verify the slot exists and status='available'.
    2. Update available_slots: status='booked', patient_id=patient_id.
    3. Insert into appointment_requests with status='confirmed'.
    4. Return confirmed appointment details.

    Returns:
        Dict with success, appointment_id, provider_name, slot_date,
        slot_time, reason.

    Raises:
        ValueError if slot not found or already booked.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Step 1: Verify slot exists and is available (lock the row)
            slot = await conn.fetchrow(
                """SELECT s.id, s.provider_id, s.slot_date, s.slot_time,
                          s.status, p.name AS provider_name
                   FROM available_slots s
                   JOIN providers p ON p.id = s.provider_id
                   WHERE s.id = $1
                   FOR UPDATE OF s""",
                slot_id,
            )
            if not slot:
                raise ValueError(f"Slot {slot_id} not found")
            if slot["status"] != "available":
                raise ValueError(
                    f"Slot {slot_id} is not available (current status: {slot['status']})"
                )

            # Step 2: Mark slot as booked
            await conn.execute(
                """UPDATE available_slots
                   SET status = 'booked', patient_id = $1
                   WHERE id = $2""",
                patient_id, slot_id,
            )

            # Step 3: Insert appointment request
            appointment_id = await conn.fetchval(
                """INSERT INTO appointment_requests
                       (patient_id, provider_id, requested_date, reason, status,
                        slot_id, notes)
                   VALUES ($1, $2, $3, $4, 'confirmed', $5, $6)
                   RETURNING id""",
                patient_id,
                slot["provider_id"],
                slot["slot_date"],
                reason,
                slot_id,
                notes,
            )

    return {
        "success": True,
        "appointment_id": appointment_id,
        "provider_name": slot["provider_name"],
        "slot_date": str(slot["slot_date"]),
        "slot_time": slot["slot_time"].strftime("%H:%M"),
        "reason": reason,
    }


async def cancel_appointment(
    patient_id: int,
    appointment_request_id: int,
) -> dict:
    """
    Cancel a confirmed appointment.

    Steps:
    1. Verify the appointment_request exists, belongs to patient_id,
       and status='confirmed'.
    2. Update appointment_requests: status='cancelled'.
    3. Update available_slots: status='available', patient_id=NULL.
    4. Return confirmation.

    Returns:
        Dict with success, message.

    Raises:
        ValueError if not found, wrong patient, or already cancelled.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Step 1: Fetch and lock the request
            request = await conn.fetchrow(
                """SELECT id, patient_id, slot_id, status
                   FROM appointment_requests
                   WHERE id = $1
                   FOR UPDATE""",
                appointment_request_id,
            )
            if not request:
                raise ValueError(
                    f"Appointment request {appointment_request_id} not found"
                )
            if request["patient_id"] != patient_id:
                raise ValueError(
                    f"Appointment request {appointment_request_id} "
                    "does not belong to this patient"
                )
            if request["status"] != "confirmed":
                raise ValueError(
                    f"Appointment request {appointment_request_id} "
                    f"cannot be cancelled (current status: {request['status']})"
                )

            # Step 2: Cancel the request
            await conn.execute(
                """UPDATE appointment_requests
                   SET status = 'cancelled'
                   WHERE id = $1""",
                appointment_request_id,
            )

            # Step 3: Free the slot
            if request["slot_id"]:
                await conn.execute(
                    """UPDATE available_slots
                       SET status = 'available', patient_id = NULL
                       WHERE id = $1""",
                    request["slot_id"],
                )

    return {
        "success": True,
        "message": f"Appointment {appointment_request_id} cancelled successfully.",
    }
