"""
Database module for Health Advisor MCP Server.
Handles SQLite database operations and schema management.
"""

import aiosqlite
import os
from pathlib import Path
from datetime import date, datetime
from typing import Optional

# Get database path from environment or use default
DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/health_advisor.db")


def get_db_path() -> str:
    """Get absolute path to database file."""
    if os.path.isabs(DATABASE_PATH):
        return DATABASE_PATH
    # Relative to project root
    project_root = Path(__file__).parent.parent
    return str(project_root / DATABASE_PATH)


async def init_database():
    """Initialize database with schema."""
    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        # Patients table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                date_of_birth DATE NOT NULL,
                gender TEXT,
                address TEXT,
                city TEXT,
                state TEXT,
                zip_code TEXT,
                phone TEXT,
                email TEXT,
                member_id TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Medical records / conditions
        await db.execute("""
            CREATE TABLE IF NOT EXISTS medical_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                condition_name TEXT NOT NULL,
                diagnosis_date DATE,
                status TEXT DEFAULT 'active',
                severity TEXT,
                notes TEXT,
                icd_code TEXT,
                FOREIGN KEY (patient_id) REFERENCES patients(id)
            )
        """)

        # Prescriptions
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prescriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                medication_name TEXT NOT NULL,
                dosage TEXT NOT NULL,
                frequency TEXT NOT NULL,
                prescribing_doctor TEXT,
                start_date DATE,
                end_date DATE,
                refills_remaining INTEGER DEFAULT 0,
                pharmacy TEXT,
                status TEXT DEFAULT 'active',
                FOREIGN KEY (patient_id) REFERENCES patients(id)
            )
        """)

        # Appointments
        await db.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                provider_name TEXT NOT NULL,
                provider_specialty TEXT,
                appointment_date DATETIME NOT NULL,
                location TEXT,
                reason TEXT,
                status TEXT DEFAULT 'scheduled',
                notes TEXT,
                FOREIGN KEY (patient_id) REFERENCES patients(id)
            )
        """)

        # Insurance information
        await db.execute("""
            CREATE TABLE IF NOT EXISTS insurance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                provider_name TEXT NOT NULL,
                plan_name TEXT,
                policy_number TEXT,
                group_number TEXT,
                subscriber_name TEXT,
                effective_date DATE,
                termination_date DATE,
                copay_amount REAL,
                deductible REAL,
                deductible_met REAL DEFAULT 0,
                FOREIGN KEY (patient_id) REFERENCES patients(id)
            )
        """)

        # Lab results
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lab_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                test_name TEXT NOT NULL,
                test_date DATE NOT NULL,
                result_value TEXT,
                unit TEXT,
                reference_range TEXT,
                status TEXT DEFAULT 'normal',
                ordering_provider TEXT,
                lab_name TEXT,
                notes TEXT,
                FOREIGN KEY (patient_id) REFERENCES patients(id)
            )
        """)

        await db.commit()


async def get_connection():
    """Get database connection."""
    return await aiosqlite.connect(get_db_path())


# Query functions for MCP tools

async def get_patient_by_id(patient_id: int) -> Optional[dict]:
    """Get patient demographics by ID."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM patients WHERE id = ?", (patient_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_all_patients() -> list[dict]:
    """Get all patients (for selector dropdown)."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, first_name, last_name, member_id FROM patients ORDER BY last_name"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_patient_conditions(patient_id: int) -> list[dict]:
    """Get medical records/conditions for a patient."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM medical_records WHERE patient_id = ? ORDER BY diagnosis_date DESC",
            (patient_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_patient_prescriptions(patient_id: int, active_only: bool = True) -> list[dict]:
    """Get prescriptions for a patient."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM prescriptions WHERE patient_id = ?"
        if active_only:
            query += " AND status = 'active'"
        query += " ORDER BY start_date DESC"
        async with db.execute(query, (patient_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_patient_appointments(patient_id: int, upcoming_only: bool = True) -> list[dict]:
    """Get appointments for a patient."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM appointments WHERE patient_id = ?"
        if upcoming_only:
            query += " AND appointment_date >= date('now') AND status = 'scheduled'"
        query += " ORDER BY appointment_date ASC"
        async with db.execute(query, (patient_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_patient_insurance(patient_id: int) -> list[dict]:
    """Get insurance information for a patient."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM insurance WHERE patient_id = ? ORDER BY effective_date DESC",
            (patient_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_patient_lab_results(patient_id: int, limit: int = 20) -> list[dict]:
    """Get lab results for a patient."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM lab_results WHERE patient_id = ? ORDER BY test_date DESC LIMIT ?",
            (patient_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
