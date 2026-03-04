"""
Patient Access Control for My Health Access.

Maps Firebase UIDs to allowed patient IDs using SQLite.

NOTE: This module is NO LONGER used for authorization.
Authorization is handled entirely via Firebase custom claims.
See backend/firebase_auth.py and backend/auth_context.py.

This file is retained as a potential admin utility for
bootstrapping claim data. It is not imported by any active
code path.
"""

import logging
import os
import aiosqlite
from pathlib import Path
from typing import Optional

logger = logging.getLogger("patient_access")

# Database path - same directory as MCP server database
DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "patient_access.db"


async def init_patient_access_db() -> None:
    """Initialize the patient_access database and table."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS patient_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_uid TEXT NOT NULL,
                patient_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_uid, patient_id)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_patient_access_uid
            ON patient_access(user_uid)
        """)
        await db.commit()
        logger.info(f"Patient access database initialized at {DB_PATH}")


async def seed_patient_access() -> None:
    """
    Seed initial patient access mappings.
    Uses placeholder UIDs that should be replaced with real Firebase UIDs.
    """
    # Placeholder mappings - replace with real UIDs after running /api/whoami
    seed_data = [
        ("UID_ALICE", 1),  # Replace with real UID for patient 1 access
        ("UID_BOB", 2),    # Replace with real UID for patient 2 access
    ]

    async with aiosqlite.connect(DB_PATH) as db:
        for user_uid, patient_id in seed_data:
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO patient_access (user_uid, patient_id) VALUES (?, ?)",
                    (user_uid, patient_id)
                )
            except Exception as e:
                logger.warning(f"Could not insert seed data ({user_uid}, {patient_id}): {e}")
        await db.commit()
        logger.info("Patient access seed data loaded")


async def get_allowed_patient_ids(user_uid: str) -> list[int]:
    """
    Get list of patient IDs the user is allowed to access.

    Args:
        user_uid: Firebase UID

    Returns:
        List of patient IDs the user can access
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT patient_id FROM patient_access WHERE user_uid = ?",
            (user_uid,)
        )
        rows = await cursor.fetchall()
        return [row["patient_id"] for row in rows]


async def check_patient_access(user_uid: str, patient_id: int) -> bool:
    """
    Check if a user has access to a specific patient.

    Args:
        user_uid: Firebase UID
        patient_id: Patient ID to check

    Returns:
        True if user has access, False otherwise
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM patient_access WHERE user_uid = ? AND patient_id = ?",
            (user_uid, patient_id)
        )
        row = await cursor.fetchone()
        return row is not None


async def grant_patient_access(user_uid: str, patient_id: int) -> bool:
    """
    Grant a user access to a patient.

    Args:
        user_uid: Firebase UID
        patient_id: Patient ID to grant access to

    Returns:
        True if access was granted, False if already exists
    """
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO patient_access (user_uid, patient_id) VALUES (?, ?)",
                (user_uid, patient_id)
            )
            await db.commit()
            logger.info(f"Granted access: uid={user_uid} -> patient_id={patient_id}")
            return True
        except aiosqlite.IntegrityError:
            # Already exists
            return False


async def revoke_patient_access(user_uid: str, patient_id: int) -> bool:
    """
    Revoke a user's access to a patient.

    Args:
        user_uid: Firebase UID
        patient_id: Patient ID to revoke access from

    Returns:
        True if access was revoked, False if didn't exist
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM patient_access WHERE user_uid = ? AND patient_id = ?",
            (user_uid, patient_id)
        )
        await db.commit()
        if cursor.rowcount > 0:
            logger.info(f"Revoked access: uid={user_uid} -> patient_id={patient_id}")
            return True
        return False


async def list_all_access() -> list[dict]:
    """
    List all patient access mappings (for debugging).

    Returns:
        List of {user_uid, patient_id} dicts
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT user_uid, patient_id, created_at FROM patient_access ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
