"""Daily CSC maintenance — auto-disable, reminders, staff alerts.

Idempotent and transaction-safe. Safe to run on startup and on a schedule.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.roles import get_allowed_roles
from app.models.institution import Institution
from app.models.user import StudentProfile, User
from app.services.audit_log import record_audit
from app.services.csc_eligibility import should_disable_for_csc_inactivity
from app.services.institution_policies import get_csc_policy
from app.services.notification_dispatch import (
    sync_staff_csc_notifications,
    sync_student_csc_notifications,
)

logger = logging.getLogger(__name__)


def run_daily_csc_job(db: Session) -> dict[str, int]:
    """Process CSC inactivity and reminders for every institution."""
    stats = {"institutions": 0, "disabled": 0, "student_notifications": 0, "staff_users": 0}

    institutions = db.query(Institution).all()
    for institution in institutions:
        stats["institutions"] += 1
        policy = get_csc_policy(db, institution.id)

        profiles = (
            db.query(StudentProfile)
            .join(User, User.id == StudentProfile.user_id)
            .filter(User.institution_id == institution.id)
            .all()
        )

        for profile in profiles:
            if policy.auto_disable and should_disable_for_csc_inactivity(profile, policy):
                prev = {"status": profile.status, "disableReason": profile.disable_reason}
                if profile.status != "inactive" or profile.disable_reason != "csc_inactivity":
                    profile.status = "inactive"
                    profile.disable_reason = "csc_inactivity"
                    stats["disabled"] += 1
                    record_audit(
                        db,
                        institution_id=institution.id,
                        actor_user_id="system",
                        actor_role="system",
                        action="csc_auto_disable",
                        entity_type="student",
                        entity_id=profile.id,
                        previous_state=prev,
                        new_state={"status": "inactive", "disableReason": "csc_inactivity"},
                    )

            if profile.last_csc_interaction_at:
                before = db.query(User).filter(User.id == profile.user_id).count()
                sync_student_csc_notifications(db, profile)
                if before:
                    stats["student_notifications"] += 1

        staff_users = db.query(User).filter(User.institution_id == institution.id).all()
        for staff in staff_users:
            allowed = get_allowed_roles(staff)
            role = "admin" if "admin" in allowed else "tutor" if "tutor" in allowed else None
            if not role:
                continue
            sync_staff_csc_notifications(db, staff, role)
            stats["staff_users"] += 1

    db.flush()
    logger.info("CSC daily job finished: %s", stats)
    return stats
