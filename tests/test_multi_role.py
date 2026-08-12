"""Multi-role account tests — admin + tutor on one phone/login."""
from __future__ import annotations

from app.core.security import hash_password
from app.models.user import User
from tests.conftest import login


def test_login_offers_role_selection_for_admin_and_tutor(client, db):
    user = User(
        id="staff-1",
        institution_id="inst-1",
        name="Branch Lead",
        email="9876543210@gmail.com",
        password_hash=hash_password("9876543210"),
        role="admin",
        roles="admin,tutor",
        is_owner=False,
    )
    db.add(user)
    db.commit()

    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": "9876543210@gmail.com",
            "password": "9876543210",
            "institutionCode": "TEST",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["type"] == "role_selection"
    roles = {item["role"] for item in data["roles"]}
    assert roles == {"admin", "tutor"}


def test_create_admin_also_tutor_merges_existing_tutor(client, db):
    tutor = User(
        id="tut-merge",
        institution_id="inst-1",
        name="Existing Tutor",
        email="8888888888@gmail.com",
        password_hash=hash_password("8888888888"),
        role="tutor",
        roles="tutor",
    )
    db.add(tutor)
    db.commit()

    token = login(client, "admin@test.edu", institution_code="TEST")
    res = client.post(
        "/api/v1/admins",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Existing Tutor",
            "phone": "8888888888",
            "centerIds": ["c1"],
            "alsoTutor": True,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["id"] == "tut-merge"
    assert set(body["roles"]) == {"admin", "tutor"}

    login_res = client.post(
        "/api/v1/auth/login",
        json={
            "email": "8888888888@gmail.com",
            "password": "8888888888",
            "institutionCode": "TEST",
        },
    )
    assert login_res.status_code == 200
    assert login_res.json()["type"] == "role_selection"


def test_create_tutor_merges_existing_admin(client, db):
    admin = User(
        id="adm-merge",
        institution_id="inst-1",
        name="Existing Admin",
        email="7777777777@gmail.com",
        password_hash=hash_password("7777777777"),
        role="admin",
        roles="admin",
        is_owner=False,
    )
    db.add(admin)
    db.commit()

    token = login(client, "admin@test.edu", institution_code="TEST")
    res = client.post(
        "/api/v1/tutors",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Existing Admin", "phone": "7777777777"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["id"] == "adm-merge"

    db.refresh(admin)
    assert admin.roles == "admin,tutor"
