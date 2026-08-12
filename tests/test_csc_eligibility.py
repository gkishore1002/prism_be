"""CSC eligibility unit tests."""
from __future__ import annotations

from datetime import date, timedelta

from app.models.user import StudentProfile, User
from app.services.csc_eligibility import (
    apply_csc_inactivity_check,
    days_until_csc_disable,
    record_csc_interaction,
    should_disable_for_csc_inactivity,
)
from app.services.institution_policies import CscPolicy


def _profile(last_visit: str | None = None) -> StudentProfile:
    user = User(id="u1", institution_id="inst-1", name="S", email="s@t.edu", password_hash="x", role="student")
    return StudentProfile(
        id="stu-1",
        user_id="u1",
        board="CBSE",
        grade="G8",
        center_id="c1",
        last_csc_interaction_at=last_visit,
        user=user,
    )


def test_never_visited_no_disable():
    profile = _profile(None)
    policy = CscPolicy.from_dict({})
    assert days_until_csc_disable(profile, policy) is None
    assert not should_disable_for_csc_inactivity(profile, policy)


def test_89_days_still_active():
    last = (date.today() - timedelta(days=89)).isoformat()
    profile = _profile(last)
    policy = CscPolicy.from_dict({})
    assert days_until_csc_disable(profile, policy) == 1
    assert not should_disable_for_csc_inactivity(profile, policy)


def test_90_days_triggers_disable():
    last = (date.today() - timedelta(days=90)).isoformat()
    profile = _profile(last)
    policy = CscPolicy.from_dict({})
    assert should_disable_for_csc_inactivity(profile, policy)


def test_reactivation_on_collection(db):
    from app.models.user import StudentProfile

    profile = db.get(StudentProfile, "stu-1")
    profile.last_csc_interaction_at = (date.today() - timedelta(days=95)).isoformat()
    profile.status = "inactive"
    profile.disable_reason = "csc_inactivity"
    db.commit()

    record_csc_interaction(db, profile, date.today().isoformat())
    db.commit()

    assert profile.status == "active"
    assert profile.disable_reason is None


def test_apply_csc_inactivity_check_blocks_login(db):
    from app.models.user import StudentProfile

    profile = db.get(StudentProfile, "stu-1")
    profile.last_csc_interaction_at = (date.today() - timedelta(days=91)).isoformat()
    profile.status = "active"
    profile.disable_reason = None
    db.commit()

    blocked = apply_csc_inactivity_check(db, profile)
    db.commit()

    assert blocked is True
    assert profile.status == "inactive"
    assert profile.disable_reason == "csc_inactivity"
