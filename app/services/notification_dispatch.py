"""Create and sync user-targeted in-app notifications."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.roles import get_allowed_roles
from app.models.assessment import Assessment
from app.models.csc import AssessmentAccessRequest
from app.models.notification import Notification
from app.models.user import StudentProfile, User
from app.services.csc_eligibility import days_until_csc_disable, login_block_message
from app.services.institution_policies import get_csc_policy

# Reassignment
REASSIGNMENT_REQUESTED = "reassignment_requested"
REASSIGNMENT_APPROVED = "reassignment_approved"
REASSIGNMENT_REJECTED = "reassignment_rejected"

# CSC — student-facing (own countdown)
CSC_REMINDER_30 = "csc_reminder_30"
CSC_REMINDER_14 = "csc_reminder_14"
CSC_REMINDER_7 = "csc_reminder_7"
CSC_INACTIVE = "csc_inactive"

# CSC — staff-facing (student at risk)
CSC_STUDENT_REMINDER_30 = "csc_student_reminder_30"
CSC_STUDENT_REMINDER_14 = "csc_student_reminder_14"
CSC_STUDENT_REMINDER_7 = "csc_student_reminder_7"
CSC_STUDENT_INACTIVE = "csc_student_inactive"

CSC_MILESTONES: tuple[tuple[str, str, int, int, str], ...] = (
    (CSC_REMINDER_30, CSC_STUDENT_REMINDER_30, 30, 14, "info"),
    (CSC_REMINDER_14, CSC_STUDENT_REMINDER_14, 14, 7, "warning"),
    (CSC_REMINDER_7, CSC_STUDENT_REMINDER_7, 7, 0, "risk"),
)

_KIND_BY_SUFFIX = {"30": "info", "14": "warning", "7": "risk"}
_TYPE_BY_SUFFIX = {
    "30": (CSC_REMINDER_30, CSC_STUDENT_REMINDER_30),
    "14": (CSC_REMINDER_14, CSC_STUDENT_REMINDER_14),
    "7": (CSC_REMINDER_7, CSC_STUDENT_REMINDER_7),
}


def _milestones_for_institution(db: Session, institution_id: str) -> tuple[tuple[str, str, int, int, str], ...]:
    policy = get_csc_policy(db, institution_id)
    items: list[tuple[str, str, int, int, str]] = []
    for upper, lower, suffix in policy.reminder_milestones():
        student_type, staff_type = _TYPE_BY_SUFFIX[suffix]
        items.append((student_type, staff_type, upper, lower, _KIND_BY_SUFFIX[suffix]))
    return tuple(items)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cycle_since(profile: StudentProfile) -> str | None:
    return profile.last_csc_interaction_at


def _has_notification_in_cycle(
    db: Session,
    *,
    user_id: str,
    ntype: str,
    entity_type: str,
    entity_id: str,
    since: str | None,
) -> bool:
    q = db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.type == ntype,
        Notification.entity_type == entity_type,
        Notification.entity_id == entity_id,
    )
    if since:
        q = q.filter(Notification.created_at >= since)
    return q.first() is not None


def create_user_notification(
    db: Session,
    *,
    user: User,
    role: str,
    ntype: str,
    title: str,
    message: str,
    kind: str = "info",
    entity_type: str | None = None,
    entity_id: str | None = None,
    href: str | None = None,
    dedupe: bool = False,
    dedupe_since: str | None = None,
) -> Notification | None:
    if dedupe and entity_type and entity_id:
        if _has_notification_in_cycle(
            db,
            user_id=user.id,
            ntype=ntype,
            entity_type=entity_type,
            entity_id=entity_id,
            since=dedupe_since,
        ):
            return None

    note = Notification(
        id=f"n-{uuid.uuid4().hex[:8]}",
        institution_id=user.institution_id,
        user_id=user.id,
        role=role,
        type=ntype,
        kind=kind,
        title=title,
        message=message,
        entity_type=entity_type,
        entity_id=entity_id,
        created_at=_now_iso(),
        read=False,
        href=href,
    )
    db.add(note)
    return note


def _student_name(db: Session, profile: StudentProfile) -> str:
    user = profile.user if profile.user else db.get(User, profile.user_id)
    return user.name if user else profile.id


def _staff_recipients(db: Session, institution_id: str) -> list[tuple[User, str]]:
    """Users who can review reassignments / see CSC alerts."""
    out: list[tuple[User, str]] = []
    users = db.query(User).filter(User.institution_id == institution_id).all()
    for u in users:
        allowed = get_allowed_roles(u)
        if "admin" in allowed:
            out.append((u, "admin"))
        elif "tutor" in allowed:
            out.append((u, "tutor"))
    return out


def notify_reassignment_requested(
    db: Session,
    req: AssessmentAccessRequest,
    assessment: Assessment,
    student: StudentProfile,
) -> None:
    student_name = _student_name(db, student)
    title = "Reassignment request"
    message = (
        f"{student_name} requested to attend “{assessment.title}” after the deadline. "
        "Review and approve or reject."
    )
    href = "/tutor/assessments"
    for user, role in _staff_recipients(db, assessment.institution_id):
        create_user_notification(
            db,
            user=user,
            role=role,
            ntype=REASSIGNMENT_REQUESTED,
            title=title,
            message=message,
            kind="warning",
            entity_type="access_request",
            entity_id=req.id,
            href=href if role == "tutor" else "/admin/assessments",
            dedupe=True,
            dedupe_since=req.requested_at,
        )
    db.flush()


def notify_reassignment_reviewed(
    db: Session,
    req: AssessmentAccessRequest,
    assessment: Assessment,
    student: StudentProfile,
    *,
    approved: bool,
) -> None:
    student_user = student.user if student.user else db.get(User, student.user_id)
    if not student_user:
        return
    if approved:
        title = "Reassignment approved"
        message = (
            f"Your tutor approved your request for “{assessment.title}”. "
            f"You can attend until {req.access_granted_until or 'the extension window'}."
        )
        ntype = REASSIGNMENT_APPROVED
        kind = "success"
    else:
        title = "Reassignment declined"
        message = (
            f"Your request for “{assessment.title}” was declined. "
            "Contact your tutor or visit your CSC center."
        )
        ntype = REASSIGNMENT_REJECTED
        kind = "risk"
    create_user_notification(
        db,
        user=student_user,
        role="student",
        ntype=ntype,
        title=title,
        message=message,
        kind=kind,
        entity_type="access_request",
        entity_id=req.id,
        href="/student/assessments",
        dedupe=True,
        dedupe_since=req.reviewed_at,
    )
    db.flush()


def _milestone_for_days(
    days: int | None,
    milestones: tuple[tuple[str, str, int, int, str], ...],
) -> tuple[str, str, int, int, str] | None:
    if days is None:
        return None
    for milestone in milestones:
        _student_type, _staff_type, upper, lower, kind = milestone
        if days <= upper and days > lower:
            return milestone
    return None


def sync_student_csc_notifications(db: Session, profile: StudentProfile) -> None:
    """Run on student login — countdown reminders and inactive notice."""
    user = profile.user
    if not user:
        return

    cycle = _cycle_since(profile)
    if not cycle:
        return

    policy = get_csc_policy(db, user.institution_id)
    milestones = _milestones_for_institution(db, user.institution_id)
    days = days_until_csc_disable(profile, policy)

    if profile.status == "inactive" and profile.disable_reason == "csc_inactivity":
        create_user_notification(
            db,
            user=user,
            role="student",
            ntype=CSC_INACTIVE,
            title="Account disabled — CSC visit required",
            message=login_block_message(profile, policy),
            kind="risk",
            entity_type="student",
            entity_id=profile.id,
            href="/student/assessments",
            dedupe=True,
            dedupe_since=cycle,
        )
        db.flush()
        return

    milestone = _milestone_for_days(days, milestones)
    if not milestone:
        return

    student_type, _, _upper, _lower, kind = milestone
    labels = {
        CSC_REMINDER_30: ("CSC visit reminder", f"Plan a CSC visit within the next 30 days ({days} days left)."),
        CSC_REMINDER_14: ("CSC visit due soon", f"Visit your CSC center within 14 days ({days} days left)."),
        CSC_REMINDER_7: ("Urgent: CSC visit needed", f"Only {days} days left before your account may be disabled."),
    }
    title, message = labels[student_type]
    create_user_notification(
        db,
        user=user,
        role="student",
        ntype=student_type,
        title=title,
        message=message,
        kind=kind,
        entity_type="student",
        entity_id=profile.id,
        href="/student/assessments",
        dedupe=True,
        dedupe_since=cycle,
    )
    db.flush()


def sync_staff_csc_notifications(db: Session, staff: User, staff_role: str) -> None:
    """Run on tutor/admin login — alert staff about students approaching CSC deadlines."""
    if staff_role not in ("tutor", "admin"):
        return

    milestones = _milestones_for_institution(db, staff.institution_id)
    policy = get_csc_policy(db, staff.institution_id)
    profiles = (
        db.query(StudentProfile)
        .join(User, User.id == StudentProfile.user_id)
        .filter(User.institution_id == staff.institution_id)
        .all()
    )
    href = f"/{staff_role}/students"

    for profile in profiles:
        cycle = _cycle_since(profile)
        if not cycle:
            continue

        student_name = _student_name(db, profile)
        days = days_until_csc_disable(profile, policy)

        if profile.status == "inactive" and profile.disable_reason == "csc_inactivity":
            create_user_notification(
                db,
                user=staff,
                role=staff_role,
                ntype=CSC_STUDENT_INACTIVE,
                title="Student inactive — CSC",
                message=(
                    f"{student_name} was disabled (no CSC visit in "
                    f"{policy.inactivity_threshold_days} days)."
                ),
                kind="risk",
                entity_type="student",
                entity_id=profile.id,
                href=href,
                dedupe=True,
                dedupe_since=cycle,
            )
            continue

        milestone = _milestone_for_days(days, milestones)
        if not milestone:
            continue

        _, staff_type, _upper, _lower, kind = milestone
        staff_labels = {
            CSC_STUDENT_REMINDER_30: (
                "CSC reminder — student",
                f"{student_name} should visit CSC within 30 days ({days} days left).",
            ),
            CSC_STUDENT_REMINDER_14: (
                "CSC warning — student",
                f"{student_name} has {days} days until CSC inactivity disable.",
            ),
            CSC_STUDENT_REMINDER_7: (
                "Urgent CSC — student",
                f"{student_name} has only {days} days before CSC inactivity disable.",
            ),
        }
        title, message = staff_labels[staff_type]
        create_user_notification(
            db,
            user=staff,
            role=staff_role,
            ntype=staff_type,
            title=title,
            message=message,
            kind=kind,
            entity_type="student",
            entity_id=profile.id,
            href=href,
            dedupe=True,
            dedupe_since=cycle,
        )

    db.flush()


def sync_user_notifications(db: Session, user: User, role: str) -> None:
    """Entry point after login — CSC sync for students and staff."""
    if role == "student":
        profile = db.query(StudentProfile).filter(StudentProfile.user_id == user.id).first()
        if profile:
            sync_student_csc_notifications(db, profile)
    elif role in ("tutor", "admin"):
        sync_staff_csc_notifications(db, user, role)
