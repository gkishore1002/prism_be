"""First-run deployment setup tests."""
from __future__ import annotations

import os

os.environ.setdefault("CSC_SCHEDULER_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.deps import get_db, get_public_db
from app.core.security import hash_password, verify_password
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.deployment import SINGLETON_ID, SystemInitialization
from app.models.institution import Institution
from app.models.user import User
import app.models  # noqa: F401


@pytest.fixture()
def fresh_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def fresh_client(fresh_db):
    def override_get_db():
        try:
            yield fresh_db
        finally:
            pass

    def override_get_public_db():
        try:
            yield fresh_db
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_public_db] = override_get_public_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


def test_setup_status_requires_setup_on_fresh_db(fresh_client):
    res = fresh_client.get("/api/v1/setup/status")
    assert res.status_code == 200
    data = res.json()
    assert data["setupRequired"] is True
    assert data["initialized"] is False


def test_setup_status_includes_default_org_code(fresh_client):
    res = fresh_client.get("/api/v1/setup/status")
    assert res.status_code == 200
    data = res.json()
    assert data["defaultOrganizationCode"] == "CSC"


def test_setup_uses_default_org_code_when_omitted(fresh_client, fresh_db):
    res = fresh_client.post(
        "/api/v1/setup",
        json={
            "organizationName": "Default Code Academy",
            "superAdminName": "Owner User",
            "superAdminPhone": "9876543210",
            "password": "secure-pass-1",
        },
    )
    assert res.status_code == 201, res.text
    inst = fresh_db.query(Institution).first()
    assert inst is not None
    assert inst.code == "CSC"


def test_first_run_setup_creates_super_admin_and_organization(fresh_client, fresh_db):
    res = fresh_client.post(
        "/api/v1/setup",
        json={
            "organizationName": "Acme Academy",
            "organizationCode": "ACME",
            "superAdminName": "Owner User",
            "superAdminPhone": "9876543210",
            "password": "secure-pass-1",
        },
    )
    assert res.status_code == 201, res.text

    inst = fresh_db.query(Institution).first()
    assert inst is not None
    assert inst.name == "Acme Academy"
    assert inst.code == "ACME"

    admin = fresh_db.query(User).filter(User.email == "9876543210@gmail.com").first()
    assert admin is not None
    assert admin.is_owner is True
    assert admin.role == "admin"
    assert verify_password("secure-pass-1", admin.password_hash)

    init = fresh_db.get(SystemInitialization, SINGLETON_ID)
    assert init is not None
    assert init.initialized_by_user_id == admin.id


def test_second_setup_rejected(fresh_client):
    payload = {
        "organizationName": "Acme Academy",
        "organizationCode": "ACME",
        "superAdminName": "Owner User",
        "superAdminPhone": "9876543210",
        "password": "secure-pass-1",
    }
    first = fresh_client.post("/api/v1/setup", json=payload)
    assert first.status_code == 201

    second = fresh_client.post(
        "/api/v1/setup",
        json={
            **payload,
            "superAdminPhone": "9123456789",
        },
    )
    assert second.status_code == 403


def test_login_email_only_after_setup(fresh_client):
    fresh_client.post(
        "/api/v1/setup",
        json={
            "organizationName": "Acme Academy",
            "organizationCode": "ACME",
            "superAdminName": "Owner User",
            "superAdminPhone": "9876543210",
            "password": "secure-pass-1",
        },
    )
    res = fresh_client.post(
        "/api/v1/auth/login",
        json={"email": "9876543210@gmail.com", "password": "secure-pass-1", "institutionCode": "ACME"},
    )
    assert res.status_code == 200, res.text
    assert "accessToken" in res.json()


def test_login_blocked_before_setup(fresh_client):
    fresh_db_user = User(
        id="adm-x",
        institution_id="inst-x",
        name="Ghost",
        email="ghost@test.edu",
        password_hash=hash_password("secure-pass-1"),
        role="admin",
        roles="admin",
        is_owner=True,
    )
    # No institution — login should fail with setup required before user lookup matters
    res = fresh_client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@test.edu", "password": "secure-pass-1"},
    )
    assert res.status_code == 503
