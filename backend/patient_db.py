"""
Direct Patient Database Access for Backend API.

Provides read-only access to patient data without MCP dependency.
Uses the shared PostgreSQL connection pool from backend.db.
"""

import logging
from typing import Optional

from backend import db

logger = logging.getLogger("patient_db")


async def get_all_patients() -> list[dict]:
    """
    Get all patients from the database.

    WARNING: This function returns ALL patients without authorization checks.
    Only use this for demo endpoints that explicitly handle auth themselves.

    Returns:
        List of patient dicts with id, first_name, last_name, member_id
    """
    try:
        rows = await db.fetch("""
            SELECT id, first_name, last_name, member_id, date_of_birth
            FROM patients
            ORDER BY last_name, first_name
        """)
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"PATIENT_DB error fetching all patients: {e}")
        raise


async def get_patients_by_ids(patient_ids: list[int]) -> list[dict]:
    """
    Get patient info for a list of patient IDs.

    Returns basic patient info needed for the UI dropdown.

    Args:
        patient_ids: List of patient IDs to fetch

    Returns:
        List of patient dicts with id, first_name, last_name, member_id
    """
    if not patient_ids:
        return []

    try:
        # PostgreSQL uses ANY($1) with array for IN clause
        rows = await db.fetch("""
            SELECT id, first_name, last_name, member_id
            FROM patients
            WHERE id = ANY($1)
            ORDER BY last_name, first_name
        """, patient_ids)
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"PATIENT_DB error fetching patients: {e}")
        raise


async def get_patient_by_id(patient_id: int) -> Optional[dict]:
    """
    Get full patient demographics by ID.

    Args:
        patient_id: Patient ID to fetch

    Returns:
        Patient dict with all demographic fields, or None if not found
    """
    try:
        row = await db.fetchrow(
            "SELECT * FROM patients WHERE id = $1", patient_id
        )
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"PATIENT_DB error fetching patient {patient_id}: {e}")
        raise
