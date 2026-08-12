"""Branch access isolation tests."""
from __future__ import annotations

import uuid

from app.core.security import hash_password
from app.models.branch_access import UserCenterAccess
from app.models.institution import Center, Institution
from app.models.user import StudentProfile, User

from tests.conftest import login


def _add_center(db, institution_id: str, center_id: str, name: str) -> Center:
    center = Center(
        id=center_id,
        institution_id=institution_id,
        name=name,
        code=center_id.upper(),
        city="City",
        active=True,
    )
    db.add(center)
    db.commit()
    return center


def _add_branch_admin(db, institution_id: str, email: str, center_ids: list[str]) -> User:
    admin = User(
        id=f"adm-{uuid.uuid4().hex[:6]}",
        institution_id=institution_id,
        name=email.split("@")[0].title(),
        email=email,
        password_hash=hash_password("demo123"),
        role="admin",
        roles="admin",
        is_owner=False,
    )
    db.add(admin)
    db.flush()
    for center_id in center_ids:
        db.add(
            UserCenterAccess(
                id=f"uca-{uuid.uuid4().hex[:6]}",
                user_id=admin.id,
                center_id=center_id,
                created_at="2026-01-01T00:00:00+00:00",
                created_by="adm-1",
            )
        )
    db.commit()
    return admin


def _add_student(db, institution_id: str, student_id: str, center_id: str) -> None:
    user = User(
        id=student_id,
        institution_id=institution_id,
        name=f"Student {student_id}",
        email=f"{student_id}@test.edu",
        password_hash=hash_password("demo123"),
        role="student",
        roles="student",
    )
    profile = StudentProfile(
        id=student_id,
        user_id=student_id,
        board="CBSE",
        grade="Grade 8",
        batch="Batch A",
        center_id=center_id,
        status="active",
    )
    db.add_all([user, profile])
    db.commit()


def test_owner_sees_all_branches(client, db):
    _add_center(db, "inst-1", "c2", "Branch Two")
    token = login(client, "admin@test.edu")
    res = client.get("/api/v1/centers", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    ids = {row["id"] for row in res.json()}
    assert {"c1", "c2"}.issubset(ids)


def test_branch_admin_cannot_see_unassigned_center(client, db):
    _add_center(db, "inst-1", "c2", "Branch Two")
    _add_center(db, "inst-1", "c3", "Branch Three")
    branch_admin = _add_branch_admin(db, "inst-1", "ravi@test.edu", ["c1", "c2"])

    token = login(client, branch_admin.email)
    res = client.get("/api/v1/centers", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    ids = {row["id"] for row in res.json()}
    assert ids == {"c1", "c2"}
    assert "c3" not in ids


def test_branch_admin_cannot_query_other_branch_students(client, db):
    _add_center(db, "inst-1", "c2", "Branch Two")
    _add_center(db, "inst-1", "c3", "Branch Three")
    _add_student(db, "inst-1", "stu-c2", "c2")
    _add_student(db, "inst-1", "stu-c3", "c3")
    branch_admin = _add_branch_admin(db, "inst-1", "ravi@test.edu", ["c1", "c2"])

    token = login(client, branch_admin.email)
    denied = client.get(
        "/api/v1/students/master?center=c3",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 403

    allowed = client.get(
        "/api/v1/students/master?center=c2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert allowed.status_code == 200
    ids = {row["id"] for row in allowed.json()["items"]}
    assert "stu-c2" in ids
    assert "stu-c3" not in ids


def test_cross_organization_branch_denied(client, db):
    other = Institution(
        id="inst-2",
        name="Other Org",
        code="OTHER",
        schema_name="public",
        type="coaching",
        board_ids="[]",
        policies_json="{}",
    )
    other_center = Center(
        id="b1",
        institution_id="inst-2",
        name="Other Branch",
        code="B1",
        city="Elsewhere",
        active=True,
    )
    db.add_all([other, other_center])
    db.commit()

    token = login(client, "admin@test.edu", institution_code="TEST")
    res = client.get(
        "/api/v1/students/master?center=b1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code in (403, 404)


def test_owner_can_assign_branch_access(client, db):
    _add_center(db, "inst-1", "c2", "Branch Two")
    branch_admin = _add_branch_admin(db, "inst-1", "priya@test.edu", ["c1"])

    owner_token = login(client, "admin@test.edu")
    res = client.put(
        f"/api/v1/admins/{branch_admin.id}/branches",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"centerIds": ["c1", "c2"]},
    )
    assert res.status_code == 200
    assert set(res.json()["centerIds"]) == {"c1", "c2"}


def test_branch_admin_cannot_manage_admins(client, db):
    branch_admin = _add_branch_admin(db, "inst-1", "ravi@test.edu", ["c1"])
    token = login(client, branch_admin.email)
    res = client.get("/api/v1/admins", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403
