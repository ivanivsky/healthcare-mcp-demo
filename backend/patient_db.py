"""
Direct Patient Database Access for Backend API.

Provides read-only access to patient data without MCP dependency.
Uses the same SQLite database as the MCP server.
"""

import logging
import os
import aiosqlite
from pathlib import Path
from typing import Optional

logger = logging.getLogger("patient_db")

# Database path - same as MCP server uses
DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/health_advisor.db")


def get_db_path() -> str:
    """Get absolute path to the patient database file."""
    if os.path.isabs(DATABASE_PATH):
        return DATABASE_PATH
    # Relative to project root (parent of backend/)
    project_root = Path(__file__).parent.parent
    return str(project_root / DATABASE_PATH)


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

    db_path = get_db_path()

    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            # Build parameterized query for IN clause
            placeholders = ",".join("?" * len(patient_ids))
            query = f"""
                SELECT id, first_name, last_name, member_id
                FROM patients
                WHERE id IN ({placeholders})
                ORDER BY last_name, first_name
            """
            async with db.execute(query, patient_ids) as cursor:
                rows = await cursor.fetchall()
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
    db_path = get_db_path()

    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM patients WHERE id = ?", (patient_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"PATIENT_DB error fetching patient {patient_id}: {e}")
        raise
