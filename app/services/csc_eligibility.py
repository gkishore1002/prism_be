"""CSC eligibility: configurable inactivity disable after missed CSC visits."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models.user import StudentProfile
from app.services.institution_policies import CscPolicy, get_csc_policy

# Backward-compatible default when DB session unavailable
CSC_INACTIVITY_DAYS = 90


def _parse_date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    raw = value.strip()[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def days_since_csc_interaction(profile: StudentProfile) -> int | None:
    last = _parse_date(profile.last_csc_interaction_at)
    if last is None:
        return None
    return (date.today() - last).days


def days_until_csc_disable(
    profile: StudentProfile,
    policy: CscPolicy | None = None,
    *,
    threshold_days: int | None = None,
    db: Session | None = None,
) -> int | None:
    """Days remaining before auto-disable; None if never collected."""
    if policy is None and db is not None:
        policy = get_csc_policy(db, profile.user.institution_id)
    days = days_since_csc_interaction(profile)
    if days is None:
        return None
    limit = threshold_days
    if limit is None and policy is not None:
        limit = policy.inactivity_threshold_days
    if limit is None:
        limit = CSC_INACTIVITY_DAYS
    return max(0, limit - days)


def should_disable_for_csc_inactivity(
    profile: StudentProfile,
    policy: CscPolicy | None = None,
) -> bool:
    if policy is not None and not policy.auto_disable:
        return False
    last = _parse_date(profile.last_csc_interaction_at)
    if last is None:
        return False
    threshold = policy.inactivity_threshold_days if policy else CSC_INACTIVITY_DAYS
    return (date.today() - last).days >= threshold


def apply_csc_inactivity_check(db: Session, profile: StudentProfile) -> bool:
    """Update profile if CSC inactivity applies. Returns True if login should be blocked."""
    policy = get_csc_policy(db, profile.user.institution_id)
    if should_disable_for_csc_inactivity(profile, policy):
        if profile.status != "inactive" or profile.disable_reason != "csc_inactivity":
            profile.status = "inactive"
            profile.disable_reason = "csc_inactivity"
            db.commit()
        return True
    return profile.status == "inactive"


def login_block_message(profile: StudentProfile, policy: CscPolicy | None = None) -> str:
    reason = profile.disable_reason or "manual"
    threshold = policy.inactivity_threshold_days if policy else CSC_INACTIVITY_DAYS
    if reason == "csc_inactivity":
        return (
            "Your account is disabled because no CSC report collection was recorded in the last "
            f"{threshold} days. Please visit your CSC center with a parent or guardian."
        )
    return "Your account is inactive. Please contact your CSC center or tutor."


def record_csc_interaction(db: Session, profile: StudentProfile, collected_at: str) -> None:
    profile.last_csc_interaction_at = (
        collected_at[:10] if collected_at else datetime.now().isoformat(timespec="minutes")[:10]
    )
    policy = get_csc_policy(db, profile.user.institution_id)
    if (
        policy.auto_reactivate_on_collection
        and profile.status == "inactive"
        and profile.disable_reason == "csc_inactivity"
    ):
        profile.status = "active"
        profile.disable_reason = None
    db.flush()
