"""Institution-level assessment and CSC policy configuration."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.institution import Institution

DEFAULT_ASSESSMENT_POLICY: dict[str, Any] = {
    "defaultExtensionDays": 3,
    "maxExtensionDays": 7,
    "allowTutorExtension": True,
    "allowAdminOverride": True,
    "requireRejectionReason": True,
    "allowMultipleRequests": False,
}

DEFAULT_CSC_POLICY: dict[str, Any] = {
    "inactivityThresholdDays": 90,
    "warningThresholdDays": 14,
    "reminder30Days": True,
    "reminder14Days": True,
    "reminder7Days": True,
    "autoDisable": True,
    "autoReactivateOnCollection": True,
}


@dataclass(frozen=True)
class AssessmentPolicy:
    default_extension_days: int
    max_extension_days: int
    allow_tutor_extension: bool
    allow_admin_override: bool
    require_rejection_reason: bool
    allow_multiple_requests: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssessmentPolicy:
        merged = {**DEFAULT_ASSESSMENT_POLICY, **data}
        return cls(
            default_extension_days=int(merged["defaultExtensionDays"]),
            max_extension_days=int(merged["maxExtensionDays"]),
            allow_tutor_extension=bool(merged["allowTutorExtension"]),
            allow_admin_override=bool(merged["allowAdminOverride"]),
            require_rejection_reason=bool(merged["requireRejectionReason"]),
            allow_multiple_requests=bool(merged["allowMultipleRequests"]),
        )


@dataclass(frozen=True)
class CscPolicy:
    inactivity_threshold_days: int
    warning_threshold_days: int
    reminder_30_days: bool
    reminder_14_days: bool
    reminder_7_days: bool
    auto_disable: bool
    auto_reactivate_on_collection: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CscPolicy:
        merged = {**DEFAULT_CSC_POLICY, **data}
        return cls(
            inactivity_threshold_days=int(merged["inactivityThresholdDays"]),
            warning_threshold_days=int(merged["warningThresholdDays"]),
            reminder_30_days=bool(merged["reminder30Days"]),
            reminder_14_days=bool(merged["reminder14Days"]),
            reminder_7_days=bool(merged["reminder7Days"]),
            auto_disable=bool(merged["autoDisable"]),
            auto_reactivate_on_collection=bool(merged["autoReactivateOnCollection"]),
        )

    def reminder_milestones(self) -> tuple[tuple[int, int, str], ...]:
        """(upper_days_inclusive, lower_days_exclusive, kind_suffix) newest-first ranges."""
        items: list[tuple[int, int, str]] = []
        if self.reminder_30_days:
            items.append((30, 14, "30"))
        if self.reminder_14_days:
            items.append((14, 7, "14"))
        if self.reminder_7_days:
            items.append((7, 0, "7"))
        return tuple(items)


def _load_payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def get_institution_policies(db: Session, institution_id: str) -> dict[str, Any]:
    inst = db.get(Institution, institution_id)
    if not inst:
        return {
            "assessment": dict(DEFAULT_ASSESSMENT_POLICY),
            "csc": dict(DEFAULT_CSC_POLICY),
        }
    payload = _load_payload(inst.policies_json)
    assessment = {**DEFAULT_ASSESSMENT_POLICY, **payload.get("assessment", {})}
    csc = {**DEFAULT_CSC_POLICY, **payload.get("csc", {})}
    return {"assessment": assessment, "csc": csc}


def get_assessment_policy(db: Session, institution_id: str) -> AssessmentPolicy:
    return AssessmentPolicy.from_dict(get_institution_policies(db, institution_id)["assessment"])


def get_csc_policy(db: Session, institution_id: str) -> CscPolicy:
    return CscPolicy.from_dict(get_institution_policies(db, institution_id)["csc"])


def save_institution_policies(
    db: Session,
    institution_id: str,
    *,
    assessment: dict[str, Any] | None = None,
    csc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inst = db.get(Institution, institution_id)
    if not inst:
        raise ValueError("Institution not found")
    current = get_institution_policies(db, institution_id)
    if assessment is not None:
        current["assessment"] = {**current["assessment"], **assessment}
    if csc is not None:
        current["csc"] = {**current["csc"], **csc}
    inst.policies_json = json.dumps(current)
    db.flush()
    return current


def validate_assessment_policy(data: dict[str, Any]) -> dict[str, Any]:
    policy = AssessmentPolicy.from_dict(data)
    if policy.default_extension_days < 1:
        raise ValueError("Default extension must be at least 1 day")
    if policy.max_extension_days < policy.default_extension_days:
        raise ValueError("Maximum extension must be ≥ default extension")
    return {
        "defaultExtensionDays": policy.default_extension_days,
        "maxExtensionDays": policy.max_extension_days,
        "allowTutorExtension": policy.allow_tutor_extension,
        "allowAdminOverride": policy.allow_admin_override,
        "requireRejectionReason": policy.require_rejection_reason,
        "allowMultipleRequests": policy.allow_multiple_requests,
    }


def validate_csc_policy(data: dict[str, Any]) -> dict[str, Any]:
    policy = CscPolicy.from_dict(data)
    if policy.inactivity_threshold_days < 1:
        raise ValueError("Inactivity threshold must be at least 1 day")
    if policy.warning_threshold_days < 1:
        raise ValueError("Warning threshold must be at least 1 day")
    return {
        "inactivityThresholdDays": policy.inactivity_threshold_days,
        "warningThresholdDays": policy.warning_threshold_days,
        "reminder30Days": policy.reminder_30_days,
        "reminder14Days": policy.reminder_14_days,
        "reminder7Days": policy.reminder_7_days,
        "autoDisable": policy.auto_disable,
        "autoReactivateOnCollection": policy.auto_reactivate_on_collection,
    }
