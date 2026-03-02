"""
Authentication context for Health Advisor.

Carries authorization data extracted from Firebase custom claims.
This is the single source of truth for what a request is authorized to do.
"""

from dataclasses import dataclass, field


@dataclass
class AuthContext:
    """
    Authorization context derived from verified Firebase JWT claims.

    This dataclass is the contract between authentication (who you are)
    and authorization (what you can do). It is built from a verified
    FirebaseUser and passed to all downstream components that need to
    make access decisions.
    """

    sub: str  # Firebase UID — immutable identity
    request_id: str  # Correlation ID for audit tracing
    actor_type: str = "human"  # "human" | "agent" | "system"
    role: str = "patient"  # From custom claims: "patient" | "caregiver" | "clinician" | "admin"
    patient_ids: list[int] = field(default_factory=list)  # From custom claims
    scopes: list[str] = field(default_factory=list)  # Reserved for future use
    delegated_sub: str | None = None  # Reserved for future use

    def can_access_patient(self, patient_id: int) -> bool:
        """
        Check if this identity is authorized to access the given patient.

        Authorization rules:
        - admin role: access to all patients
        - other roles: access only to patients in patient_ids list

        Args:
            patient_id: The patient ID to check access for

        Returns:
            True if authorized, False otherwise
        """
        if self.role == "admin":
            return True
        return patient_id in self.patient_ids
