#!/usr/bin/env python3
"""
CLI tool for managing Firebase custom claims.

Sets role and patient_ids on Firebase user accounts. This is the bootstrap
tool for granting the first admin (since the admin API requires admin claims).

Usage:
    python scripts/set_claims.py --email user@example.com --role admin
    python scripts/set_claims.py --email user@example.com --role caregiver --patient-ids 1 3
    python scripts/set_claims.py --uid xK2mPqR9abc123 --role patient --patient-ids 1
    python scripts/set_claims.py --email user@example.com --show
    python scripts/set_claims.py --email user@example.com --clear

Requires Firebase Admin SDK credentials via one of:
    - FIREBASE_SERVICE_ACCOUNT (JSON string)
    - GOOGLE_APPLICATION_CREDENTIALS (path to JSON file)
    - Application Default Credentials (gcloud auth application-default login)

Also requires:
    - FIREBASE_PROJECT_ID or GOOGLE_CLOUD_PROJECT
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to path for consistent imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def init_firebase() -> bool:
    """
    Initialize Firebase Admin SDK.

    Uses the same credential resolution order as backend/firebase_auth.py:
    1. FIREBASE_SERVICE_ACCOUNT env var (JSON string)
    2. GOOGLE_APPLICATION_CREDENTIALS env var (path to file)
    3. Application Default Credentials

    Returns:
        True if initialized successfully

    Raises:
        SystemExit if initialization fails
    """
    import firebase_admin
    from firebase_admin import credentials

    # Check if already initialized
    try:
        firebase_admin.get_app()
        return True
    except ValueError:
        pass  # Not initialized yet

    # Get project ID (required)
    project_id = os.environ.get("FIREBASE_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        print("Error: No Firebase project ID found.", file=sys.stderr)
        print("Fix: export FIREBASE_PROJECT_ID=your-project-id", file=sys.stderr)
        sys.exit(1)

    options = {"projectId": project_id}

    try:
        # Option 1: Service account JSON from env var (as string)
        sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if sa_json:
            sa_dict = json.loads(sa_json)
            cred = credentials.Certificate(sa_dict)
            firebase_admin.initialize_app(cred, options=options)
            return True

        # Option 2: GOOGLE_APPLICATION_CREDENTIALS (path to file)
        gac_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if gac_path:
            if not os.path.exists(gac_path):
                print(f"Error: GOOGLE_APPLICATION_CREDENTIALS file not found: {gac_path}", file=sys.stderr)
                sys.exit(1)
            cred = credentials.Certificate(gac_path)
            firebase_admin.initialize_app(cred, options=options)
            return True

        # Option 3: Application Default Credentials
        # Check if ADC is likely available
        adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
        is_cloud_env = os.environ.get("K_SERVICE") is not None  # Cloud Run

        if not is_cloud_env and not os.path.exists(adc_path):
            print("Error: No Firebase credentials found.", file=sys.stderr)
            print("Fix one of:", file=sys.stderr)
            print("  1. export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json", file=sys.stderr)
            print("  2. export FIREBASE_SERVICE_ACCOUNT='{...json...}'", file=sys.stderr)
            print("  3. Run: gcloud auth application-default login", file=sys.stderr)
            sys.exit(1)

        firebase_admin.initialize_app(options=options)
        return True

    except Exception as e:
        print(f"Error: Firebase initialization failed: {e}", file=sys.stderr)
        sys.exit(1)


def get_user_by_email(email: str):
    """
    Look up a Firebase user by email.

    Returns:
        firebase_admin.auth.UserRecord

    Raises:
        SystemExit if user not found
    """
    from firebase_admin import auth

    try:
        return auth.get_user_by_email(email)
    except auth.UserNotFoundError:
        print(f"Error: No user found with email: {email}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: Failed to look up user by email: {e}", file=sys.stderr)
        sys.exit(1)


def get_user_by_uid(uid: str):
    """
    Look up a Firebase user by UID.

    Returns:
        firebase_admin.auth.UserRecord

    Raises:
        SystemExit if user not found
    """
    from firebase_admin import auth

    try:
        return auth.get_user(uid)
    except auth.UserNotFoundError:
        print(f"Error: No user found with UID: {uid}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: Failed to look up user by UID: {e}", file=sys.stderr)
        sys.exit(1)


def show_claims(user) -> None:
    """Print the current custom claims for a user."""
    claims = user.custom_claims or {}

    print(f"\nCurrent claims for {user.email or '(no email)'} ({user.uid}):")

    if not claims:
        print("  (No claims set yet)")
    else:
        role = claims.get("role", "(not set)")
        patient_ids = claims.get("patient_ids", [])
        actor_type = claims.get("actor_type", "(not set)")

        print(f"  Role:        {role}")
        print(f"  Patient IDs: {patient_ids}")
        print(f"  Actor type:  {actor_type}")

        # Show any other claims
        other_claims = {k: v for k, v in claims.items() if k not in ("role", "patient_ids", "actor_type")}
        if other_claims:
            print(f"  Other:       {other_claims}")


def set_claims(user, role: str, patient_ids: list[int]) -> None:
    """
    Set custom claims on a Firebase user.

    Args:
        user: firebase_admin.auth.UserRecord
        role: One of patient, caregiver, clinician, admin
        patient_ids: List of patient IDs the user can access
    """
    from firebase_admin import auth

    claims = {
        "role": role,
        "patient_ids": patient_ids,
        "actor_type": "human",
    }

    try:
        auth.set_custom_user_claims(user.uid, claims)

        print(f"\n\u2713 Claims set successfully")
        print(f"  UID:         {user.uid}")
        print(f"  Email:       {user.email or '(no email)'}")
        print(f"  Role:        {role}")
        print(f"  Patient IDs: {patient_ids}")
        print()
        print("\u2192 User must sign out and sign back in for new claims to take effect.")

    except Exception as e:
        print(f"Error: Failed to set claims: {e}", file=sys.stderr)
        sys.exit(1)


def clear_claims(user) -> None:
    """
    Remove all custom claims from a Firebase user.

    Args:
        user: firebase_admin.auth.UserRecord
    """
    from firebase_admin import auth

    try:
        auth.set_custom_user_claims(user.uid, None)

        print(f"\n\u2713 Claims cleared successfully")
        print(f"  UID:         {user.uid}")
        print(f"  Email:       {user.email or '(no email)'}")
        print()
        print("\u2192 User must sign out and sign back in for changes to take effect.")

    except Exception as e:
        print(f"Error: Failed to clear claims: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Set Firebase custom claims for user authorization.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Grant admin role (full access)
  python scripts/set_claims.py --email admin@example.com --role admin

  # Grant caregiver role with access to specific patients
  python scripts/set_claims.py --email caregiver@example.com --role caregiver --patient-ids 1 3

  # Grant patient role with access to their own record
  python scripts/set_claims.py --uid xK2mPqR9abc123 --role patient --patient-ids 1

  # View current claims
  python scripts/set_claims.py --email user@example.com --show

  # Clear all claims (reset to no access)
  python scripts/set_claims.py --email user@example.com --clear
        """,
    )

    # User identification (one required)
    id_group = parser.add_mutually_exclusive_group()
    id_group.add_argument(
        "--email",
        help="User's email address (will look up UID)",
    )
    id_group.add_argument(
        "--uid",
        help="User's Firebase UID (used directly)",
    )

    # Actions
    parser.add_argument(
        "--role",
        choices=["patient", "caregiver", "clinician", "admin"],
        help="Role to assign (required unless --show or --clear)",
    )
    parser.add_argument(
        "--patient-ids",
        type=int,
        nargs="+",
        default=[],
        metavar="ID",
        help="Patient IDs the user can access (space-separated integers)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show current claims without making changes",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove all custom claims from the user",
    )

    args = parser.parse_args()

    # Validate: need either --email or --uid
    if not args.email and not args.uid:
        parser.error("Either --email or --uid is required")

    # Validate: need --role unless --show or --clear
    if not args.show and not args.clear and not args.role:
        parser.error("--role is required unless using --show or --clear")

    # Validate: --show and --clear are mutually exclusive
    if args.show and args.clear:
        parser.error("--show and --clear cannot be used together")

    # Initialize Firebase
    init_firebase()

    # Look up the user
    if args.uid:
        user = get_user_by_uid(args.uid)
    else:
        user = get_user_by_email(args.email)

    # Execute the requested action
    if args.show:
        show_claims(user)
    elif args.clear:
        clear_claims(user)
    else:
        set_claims(user, args.role, args.patient_ids)


if __name__ == "__main__":
    main()
