"""Shared pytest fixtures for Prism API tests."""
from __future__ import annotations

import os

# Disable CSC scheduler before app import
os.environ.setdefault("CSC_SCHEDULER_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.deps import get_db, get_public_db
from app.core.security import hash_password
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.institution import Center, Institution
from app.models.user import StudentProfile, User
import app.models  # noqa: F401


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    _seed(session)
    yield session
    session.close()


@pytest.fixture()
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass

    def override_get_public_db():
        try:
            yield db
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_public_db] = override_get_public_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


def _seed(db) -> None:
    inst = Institution(
        id="inst-1",
        name="Test Academy",
        code="TEST",
        schema_name="public",
        type="coaching",
        board_ids='["cbse"]',
        policies_json="{}",
    )
    center = Center(id="c1", institution_id="inst-1", name="HQ", city="Test City", active=True)
    admin = User(
        id="adm-1",
        institution_id="inst-1",
        name="Admin",
        email="admin@test.edu",
        password_hash=hash_password("demo123"),
        role="admin",
        roles="admin",
        is_owner=True,
    )
    tutor = User(
        id="tut-1",
        institution_id="inst-1",
        name="Tutor",
        email="tutor@test.edu",
        password_hash=hash_password("demo123"),
        role="tutor",
        roles="tutor",
    )
    student_user = User(
        id="stu-1",
        institution_id="inst-1",
        name="Student",
        email="student@test.edu",
        password_hash=hash_password("demo123"),
        role="student",
        roles="student",
    )
    profile = StudentProfile(
        id="stu-1",
        user_id="stu-1",
        board="CBSE",
        grade="Grade 8",
        batch="Batch A",
        center_id="c1",
        status="active",
    )
    db.add_all([inst, center, admin, tutor, student_user, profile])
    db.commit()


def login(client: TestClient, email: str, institution_code: str | None = None) -> str:
    body: dict[str, str] = {"email": email, "password": "demo123"}
    if institution_code:
        body["institutionCode"] = institution_code
    res = client.post("/api/v1/auth/login", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    if "accessToken" in data:
        return data["accessToken"]
    raise AssertionError(f"Expected token, got role selection: {data}")
