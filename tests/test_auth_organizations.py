"""Login organization listing tests."""
from __future__ import annotations

from tests.conftest import login


def test_list_login_organizations(client):
    res = client.get("/api/v1/auth/organizations")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert data[0]["name"] == "Test Academy"
    assert data[0]["code"] == "TEST"


def test_login_with_selected_organization(client):
    token = login(client, "admin@test.edu", institution_code="TEST")
    assert token
