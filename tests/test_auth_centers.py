"""Authorization and center validation API tests."""
from __future__ import annotations

from datetime import date, timedelta

from app.models.assessment import Assessment
from app.models.user import StudentProfile, User
from app.core.security import hash_password

from tests.conftest import login


def test_student_cannot_list_all_students(client, db):
    token = login(client, "student@test.edu")
    res = client.get("/api/v1/students", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_tutor_can_list_students(client):
    token = login(client, "tutor@test.edu")
    res = client.get("/api/v1/students", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200


def test_student_cannot_read_peer_profile(client, db):
    other_user = User(
        id="stu-2",
        institution_id="inst-1",
        name="Other",
        email="other@test.edu",
        password_hash=hash_password("demo123"),
        role="student",
    )
    other = StudentProfile(
        id="stu-2",
        user_id="stu-2",
        board="CBSE",
        grade="Grade 8",
        center_id="c1",
    )
    db.add_all([other_user, other])
    db.commit()

    token = login(client, "student@test.edu")
    res = client.get("/api/v1/students/stu-2", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_invalid_center_rejected_on_student_update(client, db):
    token = login(client, "admin@test.edu")
    res = client.patch(
        "/api/v1/students/stu-1",
        headers={"Authorization": f"Bearer {token}"},
        json={"centerId": "c-nonexistent"},
    )
    assert res.status_code == 404


def test_csc_scheduler_disables_inactive_student(db):
    from app.services.csc_scheduler import run_daily_csc_job

    last = (date.today() - timedelta(days=91)).isoformat()
    profile = db.get(StudentProfile, "stu-1")
    profile.last_csc_interaction_at = last
    db.commit()

    stats = run_daily_csc_job(db)
    db.commit()
    db.refresh(profile)

    assert stats["disabled"] >= 1
    assert profile.status == "inactive"
    assert profile.disable_reason == "csc_inactivity"
