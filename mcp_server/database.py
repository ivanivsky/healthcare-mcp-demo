"""
Database module for Health Advisor MCP Server.
Uses shared PostgreSQL connection pool from backend.db.
"""

import sys
from pathlib import Path
from typing import Optional

# Add project root to path for backend module import
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import db


async def init_database():
    """Initialize database with PostgreSQL schema.

    This is called from scripts/seed_database.py.
    The actual schema creation is handled there.
    This function now just ensures the pool is ready.
    """
    await db.get_pool()


# Query functions for MCP tools

async def get_patient_by_id(patient_id: int) -> Optional[dict]:
    """Get patient demographics by ID."""
    row = await db.fetchrow(
        "SELECT * FROM patients WHERE id = $1", patient_id
    )
    return dict(row) if row else None


async def get_all_patients() -> list[dict]:
    """Get all patients (for selector dropdown)."""
    rows = await db.fetch(
        "SELECT id, first_name, last_name, member_id FROM patients ORDER BY last_name"
    )
    return [dict(row) for row in rows]


async def get_patient_conditions(patient_id: int) -> list[dict]:
    """Get medical records/conditions for a patient."""
    rows = await db.fetch(
        "SELECT * FROM medical_records WHERE patient_id = $1 ORDER BY diagnosis_date DESC",
        patient_id
    )
    return [dict(row) for row in rows]


async def get_patient_prescriptions(patient_id: int, active_only: bool = True) -> list[dict]:
    """Get prescriptions for a patient."""
    if active_only:
        rows = await db.fetch(
            "SELECT * FROM prescriptions WHERE patient_id = $1 AND status = 'active' ORDER BY start_date DESC",
            patient_id
        )
    else:
        rows = await db.fetch(
            "SELECT * FROM prescriptions WHERE patient_id = $1 ORDER BY start_date DESC",
            patient_id
        )
    return [dict(row) for row in rows]


async def get_patient_appointments(patient_id: int, upcoming_only: bool = True) -> list[dict]:
    """Get appointments for a patient."""
    if upcoming_only:
        rows = await db.fetch(
            "SELECT * FROM appointments WHERE patient_id = $1 AND appointment_date >= CURRENT_DATE AND status = 'scheduled' ORDER BY appointment_date ASC",
            patient_id
        )
    else:
        rows = await db.fetch(
            "SELECT * FROM appointments WHERE patient_id = $1 ORDER BY appointment_date ASC",
            patient_id
        )
    return [dict(row) for row in rows]


async def get_patient_insurance(patient_id: int) -> list[dict]:
    """Get insurance information for a patient."""
    rows = await db.fetch(
        "SELECT * FROM insurance WHERE patient_id = $1 ORDER BY effective_date DESC",
        patient_id
    )
    return [dict(row) for row in rows]


async def get_patient_lab_results(patient_id: int, limit: int = 20) -> list[dict]:
    """Get lab results for a patient."""
    rows = await db.fetch(
        "SELECT * FROM lab_results WHERE patient_id = $1 ORDER BY test_date DESC LIMIT $2",
        patient_id, limit
    )
    return [dict(row) for row in rows]
