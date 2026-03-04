"""
Seed database with fake patient data for Health Advisor demo.

5 Patients:
1. Complex case - multiple chronic conditions, many prescriptions
2. Healthy baseline - minimal medical history
3. Active care - upcoming appointments, recent lab results
4. Edge cases - insurance issues, gaps in care
5. Sensitive diagnoses - for testing data filtering/privacy
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.database import init_database, get_db_path
import aiosqlite


async def seed_patients(db):
    """Seed patient demographics. Idempotent - uses INSERT OR IGNORE."""
    patients = [
        # Patient 1: Complex case - elderly with multiple chronic conditions
        (
            1, "Margaret", "Chen", "1948-03-15", "Female",
            "2847 Oak Valley Drive", "San Francisco", "CA", "94122",
            "415-555-0101", "margaret.chen@email.com", "MEM-001-2024"
        ),
        # Patient 2: Healthy baseline - young adult, minimal history
        (
            2, "James", "Wilson", "1995-07-22", "Male",
            "1523 Pine Street Apt 4B", "San Francisco", "CA", "94109",
            "415-555-0102", "jwilson95@email.com", "MEM-002-2024"
        ),
        # Patient 3: Active care - middle-aged, ongoing treatment
        (
            3, "Sofia", "Rodriguez", "1978-11-08", "Female",
            "892 Mission Bay Blvd", "San Francisco", "CA", "94158",
            "415-555-0103", "sofia.r@email.com", "MEM-003-2024"
        ),
        # Patient 4: Edge cases - insurance issues, gaps in care
        (
            4, "Robert", "Thompson", "1962-04-30", "Male",
            "456 Market Street", "Oakland", "CA", "94612",
            "510-555-0104", "rthompson62@email.com", "MEM-004-2024"
        ),
        # Patient 5: Sensitive diagnoses - for privacy testing
        (
            5, "Emily", "Nakamura", "1989-12-03", "Female",
            "731 Castro Street", "San Francisco", "CA", "94114",
            "415-555-0105", "emily.nakamura@email.com", "MEM-005-2024"
        ),
    ]

    await db.executemany("""
        INSERT OR IGNORE INTO patients
        (id, first_name, last_name, date_of_birth, gender, address, city, state, zip_code, phone, email, member_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, patients)


async def seed_medical_records(db):
    """Seed medical conditions/diagnoses. Idempotent - uses INSERT OR IGNORE."""
    records = [
        # Patient 1 (Margaret) - Complex chronic conditions
        (1, 1, "Type 2 Diabetes Mellitus", "2010-05-20", "active", "moderate", "Well controlled with medication", "E11.9"),
        (2, 1, "Hypertension", "2008-03-15", "active", "moderate", "On multiple medications", "I10"),
        (3, 1, "Hyperlipidemia", "2012-08-10", "active", "mild", "Managed with statins", "E78.5"),
        (4, 1, "Osteoarthritis", "2018-11-22", "active", "moderate", "Bilateral knee involvement", "M17.0"),
        (5, 1, "Chronic Kidney Disease Stage 2", "2020-02-14", "active", "mild", "Monitoring required", "N18.2"),
        (6, 1, "Atrial Fibrillation", "2019-07-30", "active", "moderate", "Rate controlled", "I48.91"),

        # Patient 2 (James) - Minimal history
        (7, 2, "Seasonal Allergies", "2015-04-01", "active", "mild", "Spring pollen", "J30.1"),

        # Patient 3 (Sofia) - Active ongoing care
        (8, 3, "Breast Cancer", "2024-01-15", "active", "moderate", "Stage IIA, currently in treatment", "C50.919"),
        (9, 3, "Anxiety Disorder", "2020-06-10", "active", "mild", "Well managed", "F41.1"),
        (10, 3, "Anemia", "2024-02-20", "active", "mild", "Treatment-related", "D64.9"),

        # Patient 4 (Robert) - Gaps in care, multiple issues
        (11, 4, "Type 2 Diabetes Mellitus", "2015-09-10", "active", "severe", "Poor control, gaps in care", "E11.65"),
        (12, 4, "Diabetic Neuropathy", "2020-03-15", "active", "moderate", "Peripheral neuropathy", "E11.42"),
        (13, 4, "Hypertension", "2016-02-28", "active", "severe", "Uncontrolled", "I10"),
        (14, 4, "Depression", "2019-11-01", "active", "moderate", "Not currently treated", "F32.1"),

        # Patient 5 (Emily) - Sensitive diagnoses
        (15, 5, "HIV Infection", "2018-08-15", "active", "stable", "Undetectable viral load on ART", "B20"),
        (16, 5, "Generalized Anxiety Disorder", "2017-03-20", "active", "mild", "Well controlled", "F41.1"),
        (17, 5, "Gender Dysphoria", "2019-05-10", "active", "stable", "On hormone therapy", "F64.0"),
    ]

    await db.executemany("""
        INSERT OR IGNORE INTO medical_records
        (id, patient_id, condition_name, diagnosis_date, status, severity, notes, icd_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, records)


async def seed_prescriptions(db):
    """Seed prescription medications. Idempotent - uses INSERT OR IGNORE."""
    prescriptions = [
        # Patient 1 (Margaret) - Many medications for chronic conditions
        (1, 1, "Metformin", "1000mg", "Twice daily", "Dr. Sarah Kim", "2024-01-15", None, 3, "CVS Pharmacy", "active"),
        (2, 1, "Lisinopril", "20mg", "Once daily", "Dr. Sarah Kim", "2024-01-15", None, 3, "CVS Pharmacy", "active"),
        (3, 1, "Atorvastatin", "40mg", "Once daily at bedtime", "Dr. Sarah Kim", "2024-01-15", None, 3, "CVS Pharmacy", "active"),
        (4, 1, "Amlodipine", "10mg", "Once daily", "Dr. Sarah Kim", "2024-01-15", None, 3, "CVS Pharmacy", "active"),
        (5, 1, "Eliquis", "5mg", "Twice daily", "Dr. Michael Park", "2024-03-01", None, 2, "CVS Pharmacy", "active"),
        (6, 1, "Metoprolol", "50mg", "Twice daily", "Dr. Michael Park", "2024-03-01", None, 2, "CVS Pharmacy", "active"),
        (7, 1, "Acetaminophen", "500mg", "As needed for pain", "Dr. Sarah Kim", "2024-06-01", None, 0, "CVS Pharmacy", "active"),

        # Patient 2 (James) - Minimal
        (8, 2, "Cetirizine", "10mg", "Once daily as needed", "Dr. Lisa Chen", "2024-03-15", None, 2, "Walgreens", "active"),

        # Patient 3 (Sofia) - Cancer treatment
        (9, 3, "Tamoxifen", "20mg", "Once daily", "Dr. Rebecca Moore", "2024-02-01", None, 5, "UCSF Pharmacy", "active"),
        (10, 3, "Ondansetron", "8mg", "As needed for nausea", "Dr. Rebecca Moore", "2024-02-01", None, 2, "UCSF Pharmacy", "active"),
        (11, 3, "Sertraline", "50mg", "Once daily", "Dr. James Lee", "2024-01-10", None, 5, "CVS Pharmacy", "active"),
        (12, 3, "Ferrous Sulfate", "325mg", "Once daily", "Dr. Rebecca Moore", "2024-03-01", None, 3, "UCSF Pharmacy", "active"),

        # Patient 4 (Robert) - Spotty compliance
        (13, 4, "Metformin", "500mg", "Twice daily", "Dr. David Brown", "2023-06-15", None, 0, "Walgreens", "active"),
        (14, 4, "Glipizide", "10mg", "Twice daily", "Dr. David Brown", "2023-06-15", None, 0, "Walgreens", "active"),
        (15, 4, "Lisinopril", "40mg", "Once daily", "Dr. David Brown", "2023-06-15", None, 0, "Walgreens", "active"),
        (16, 4, "Gabapentin", "300mg", "Three times daily", "Dr. David Brown", "2023-09-01", None, 0, "Walgreens", "active"),

        # Patient 5 (Emily) - HIV and hormone therapy
        (17, 5, "Biktarvy", "1 tablet", "Once daily", "Dr. Amanda Foster", "2024-01-01", None, 5, "Alto Pharmacy", "active"),
        (18, 5, "Estradiol", "2mg", "Once daily", "Dr. Jennifer Walsh", "2024-02-15", None, 5, "Alto Pharmacy", "active"),
        (19, 5, "Spironolactone", "100mg", "Once daily", "Dr. Jennifer Walsh", "2024-02-15", None, 5, "Alto Pharmacy", "active"),
        (20, 5, "Buspirone", "10mg", "Twice daily", "Dr. James Lee", "2024-01-20", None, 3, "Alto Pharmacy", "active"),
    ]

    await db.executemany("""
        INSERT OR IGNORE INTO prescriptions
        (id, patient_id, medication_name, dosage, frequency, prescribing_doctor, start_date, end_date, refills_remaining, pharmacy, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, prescriptions)


async def seed_appointments(db):
    """Seed appointments. Idempotent - uses INSERT OR IGNORE."""
    appointments = [
        # Patient 1 (Margaret) - Regular follow-ups
        (1, 1, "Dr. Sarah Kim", "Internal Medicine", "2025-02-15 09:30:00", "UCSF Medical Center", "Diabetes follow-up", "scheduled", None),
        (2, 1, "Dr. Michael Park", "Cardiology", "2025-02-28 14:00:00", "UCSF Cardiology Clinic", "AFib monitoring", "scheduled", None),
        (3, 1, "Dr. Sarah Kim", "Internal Medicine", "2024-11-15 09:30:00", "UCSF Medical Center", "Annual physical", "completed", "Labs ordered"),

        # Patient 2 (James) - Infrequent visits
        (4, 2, "Dr. Lisa Chen", "Family Medicine", "2025-03-20 10:00:00", "One Medical - SOMA", "Annual checkup", "scheduled", None),

        # Patient 3 (Sofia) - Active oncology care
        (5, 3, "Dr. Rebecca Moore", "Oncology", "2025-02-05 08:00:00", "UCSF Cancer Center", "Chemotherapy cycle 4", "scheduled", None),
        (6, 3, "Dr. Rebecca Moore", "Oncology", "2025-02-19 08:00:00", "UCSF Cancer Center", "Chemotherapy cycle 5", "scheduled", None),
        (7, 3, "Dr. Rebecca Moore", "Oncology", "2025-03-05 08:00:00", "UCSF Cancer Center", "Chemotherapy cycle 6", "scheduled", None),
        (8, 3, "Dr. James Lee", "Psychiatry", "2025-02-10 15:00:00", "Telehealth", "Anxiety management", "scheduled", None),
        (9, 3, "Lab Services", "Laboratory", "2025-02-03 07:30:00", "UCSF Lab", "Pre-chemo bloodwork", "scheduled", None),

        # Patient 4 (Robert) - Gaps, some missed
        (10, 4, "Dr. David Brown", "Internal Medicine", "2024-09-15 11:00:00", "Highland Hospital", "Diabetes follow-up", "no-show", "Patient did not attend"),
        (11, 4, "Dr. David Brown", "Internal Medicine", "2025-02-20 11:00:00", "Highland Hospital", "Urgent: Diabetes management", "scheduled", None),

        # Patient 5 (Emily) - Regular HIV and gender care
        (12, 5, "Dr. Amanda Foster", "Infectious Disease", "2025-02-25 10:30:00", "SF General - Ward 86", "HIV follow-up, labs", "scheduled", None),
        (13, 5, "Dr. Jennifer Walsh", "Endocrinology", "2025-03-10 14:00:00", "UCSF Gender Health", "Hormone therapy follow-up", "scheduled", None),
        (14, 5, "Dr. James Lee", "Psychiatry", "2025-02-12 16:00:00", "Telehealth", "Anxiety check-in", "scheduled", None),
    ]

    await db.executemany("""
        INSERT OR IGNORE INTO appointments
        (id, patient_id, provider_name, provider_specialty, appointment_date, location, reason, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, appointments)


async def seed_insurance(db):
    """Seed insurance information. Idempotent - uses INSERT OR IGNORE."""
    insurance = [
        # Patient 1 (Margaret) - Medicare
        (1, 1, "Medicare", "Medicare Part B", "1EG4-TE5-MK72", None, "Margaret Chen", "2013-03-15", None, 20.00, 226.00, 226.00),
        (2, 1, "AARP Medicare Supplement", "Plan G", "AARP-445512", "GRP-8821", "Margaret Chen", "2013-03-15", None, 0.00, 0.00, 0.00),

        # Patient 2 (James) - Employer insurance
        (3, 2, "Blue Shield of California", "PPO Gold", "BSC-998877123", "TECH-5500", "James Wilson", "2023-01-01", None, 30.00, 1500.00, 450.00),

        # Patient 3 (Sofia) - Good employer coverage
        (4, 3, "Kaiser Permanente", "Platinum HMO", "KP-112233445", "SFUSD-2000", "Sofia Rodriguez", "2022-09-01", None, 20.00, 500.00, 500.00),

        # Patient 4 (Robert) - Insurance issues, Medi-Cal
        (5, 4, "Medi-Cal", "Managed Care", "MC-94612-8845", None, "Robert Thompson", "2024-01-01", None, 0.00, 0.00, 0.00),
        (6, 4, "Blue Cross", "Bronze HMO", "BC-554433", "SM-BIZ-100", "Robert Thompson", "2022-01-01", "2023-06-30", 50.00, 5000.00, 1200.00),

        # Patient 5 (Emily) - Employer insurance
        (7, 5, "Anthem Blue Cross", "Platinum PPO", "ANT-667788990", "STARTUP-100", "Emily Nakamura", "2023-06-01", None, 25.00, 750.00, 750.00),
    ]

    await db.executemany("""
        INSERT OR IGNORE INTO insurance
        (id, patient_id, provider_name, plan_name, policy_number, group_number, subscriber_name, effective_date, termination_date, copay_amount, deductible, deductible_met)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, insurance)


async def seed_lab_results(db):
    """Seed lab results. Idempotent - uses INSERT OR IGNORE."""
    lab_results = [
        # Patient 1 (Margaret) - Comprehensive metabolic panel, A1C, lipids
        (1, 1, "Hemoglobin A1C", "2024-12-15", "7.2", "%", "4.0-5.6", "abnormal", "Dr. Sarah Kim", "Quest Diagnostics", "Slightly elevated, improved from 7.8"),
        (2, 1, "Fasting Glucose", "2024-12-15", "142", "mg/dL", "70-100", "abnormal", "Dr. Sarah Kim", "Quest Diagnostics", None),
        (3, 1, "Creatinine", "2024-12-15", "1.4", "mg/dL", "0.6-1.2", "abnormal", "Dr. Sarah Kim", "Quest Diagnostics", "CKD monitoring"),
        (4, 1, "eGFR", "2024-12-15", "52", "mL/min", ">60", "abnormal", "Dr. Sarah Kim", "Quest Diagnostics", "Stage 3a CKD"),
        (5, 1, "Total Cholesterol", "2024-12-15", "185", "mg/dL", "<200", "normal", "Dr. Sarah Kim", "Quest Diagnostics", None),
        (6, 1, "LDL Cholesterol", "2024-12-15", "95", "mg/dL", "<100", "normal", "Dr. Sarah Kim", "Quest Diagnostics", "At goal"),
        (7, 1, "HDL Cholesterol", "2024-12-15", "52", "mg/dL", ">40", "normal", "Dr. Sarah Kim", "Quest Diagnostics", None),
        (8, 1, "INR", "2024-12-20", "2.3", "", "2.0-3.0", "normal", "Dr. Michael Park", "UCSF Lab", "Therapeutic range"),

        # Patient 2 (James) - Basic annual labs
        (9, 2, "Complete Blood Count", "2024-03-10", "Normal", "", "", "normal", "Dr. Lisa Chen", "LabCorp", "All values within normal limits"),
        (10, 2, "Comprehensive Metabolic Panel", "2024-03-10", "Normal", "", "", "normal", "Dr. Lisa Chen", "LabCorp", "All values within normal limits"),

        # Patient 3 (Sofia) - Oncology labs
        (11, 3, "White Blood Cell Count", "2025-01-20", "3.2", "K/uL", "4.5-11.0", "abnormal", "Dr. Rebecca Moore", "UCSF Lab", "Expected with chemotherapy"),
        (12, 3, "Hemoglobin", "2025-01-20", "10.8", "g/dL", "12.0-16.0", "abnormal", "Dr. Rebecca Moore", "UCSF Lab", "Anemia - on iron supplement"),
        (13, 3, "Platelet Count", "2025-01-20", "145", "K/uL", "150-400", "abnormal", "Dr. Rebecca Moore", "UCSF Lab", "Slightly low"),
        (14, 3, "Absolute Neutrophil Count", "2025-01-20", "1.8", "K/uL", ">1.5", "normal", "Dr. Rebecca Moore", "UCSF Lab", "OK to proceed with chemo"),
        (15, 3, "CA 27-29", "2025-01-15", "32", "U/mL", "<38", "normal", "Dr. Rebecca Moore", "UCSF Lab", "Tumor marker stable"),

        # Patient 4 (Robert) - Poor control
        (16, 4, "Hemoglobin A1C", "2023-06-10", "10.2", "%", "4.0-5.6", "critical", "Dr. David Brown", "Highland Lab", "Very poor control"),
        (17, 4, "Fasting Glucose", "2023-06-10", "245", "mg/dL", "70-100", "critical", "Dr. David Brown", "Highland Lab", None),
        (18, 4, "Creatinine", "2023-06-10", "1.6", "mg/dL", "0.6-1.2", "abnormal", "Dr. David Brown", "Highland Lab", None),

        # Patient 5 (Emily) - HIV and hormone labs
        (19, 5, "HIV Viral Load", "2024-12-01", "<20", "copies/mL", "<20", "normal", "Dr. Amanda Foster", "SF General Lab", "Undetectable"),
        (20, 5, "CD4 Count", "2024-12-01", "685", "cells/uL", ">500", "normal", "Dr. Amanda Foster", "SF General Lab", "Excellent immune function"),
        (21, 5, "Estradiol", "2024-12-15", "185", "pg/mL", "100-200", "normal", "Dr. Jennifer Walsh", "UCSF Lab", "Therapeutic range"),
        (22, 5, "Testosterone", "2024-12-15", "28", "ng/dL", "<50", "normal", "Dr. Jennifer Walsh", "UCSF Lab", "Appropriately suppressed"),
        (23, 5, "Comprehensive Metabolic Panel", "2024-12-01", "Normal", "", "", "normal", "Dr. Amanda Foster", "SF General Lab", None),
    ]

    await db.executemany("""
        INSERT OR IGNORE INTO lab_results
        (id, patient_id, test_name, test_date, result_value, unit, reference_range, status, ordering_provider, lab_name, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, lab_results)


async def main():
    """
    Initialize and seed the database.

    This function is IDEMPOTENT — it is safe to call on every startup.
    All INSERT statements use INSERT OR IGNORE, so existing data is preserved.
    This is essential for Cloud Run where containers are stateless.
    """
    print("Initializing database schema...")
    await init_database()

    db_path = get_db_path()
    print(f"Database path: {db_path}")

    async with aiosqlite.connect(db_path) as db:
        print("Seeding patients...")
        await seed_patients(db)

        print("Seeding medical records...")
        await seed_medical_records(db)

        print("Seeding prescriptions...")
        await seed_prescriptions(db)

        print("Seeding appointments...")
        await seed_appointments(db)

        print("Seeding insurance...")
        await seed_insurance(db)

        print("Seeding lab results...")
        await seed_lab_results(db)

        await db.commit()

        # Report final state
        async with db.execute("SELECT COUNT(*) FROM patients") as cursor:
            count = (await cursor.fetchone())[0]
            print(f"\nDatabase ready with {count} patients.")


if __name__ == "__main__":
    asyncio.run(main())
