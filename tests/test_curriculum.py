"""Curriculum hierarchy CRUD tests."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.academic import Board, Grade, Subject
from tests.conftest import login


def _auth(client: TestClient) -> dict[str, str]:
    token = login(client, "admin@test.edu", "TEST")
    return {"Authorization": f"Bearer {token}"}


def test_add_board_grade_and_subject(client: TestClient, db: Session) -> None:
    headers = _auth(client)

    board_res = client.post("/api/v1/curriculum/boards", json={"name": "CBSE"}, headers=headers)
    assert board_res.status_code == 201, board_res.text

    grade_res = client.post(
        "/api/v1/curriculum/grades",
        json={"board": "CBSE", "grade": "Grade 10"},
        headers=headers,
    )
    assert grade_res.status_code == 201, grade_res.text
    assert grade_res.json()["grade"] == "Grade 10"

    subject_res = client.post(
        "/api/v1/curriculum/subjects",
        json={"board": "CBSE", "grade": "Grade 10", "subject": "Science"},
        headers=headers,
    )
    assert subject_res.status_code == 201, subject_res.text

    board = db.query(Board).filter(Board.name == "CBSE").one()
    grade = db.query(Grade).filter(Grade.board_id == board.id, Grade.name == "Grade 10").one()
    subjects = db.query(Subject).filter(Subject.grade_id == grade.id).all()
    assert len(subjects) == 2
    assert {s.name for s in subjects} == {"Mathematics", "Science"}
    assert all(len(s.id) <= 32 for s in subjects)
    assert len(grade.id) <= 32


def test_add_grade_requires_board(client: TestClient) -> None:
    headers = _auth(client)
    res = client.post(
        "/api/v1/curriculum/grades",
        json={"board": "Missing", "grade": "Grade 9"},
        headers=headers,
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "Board not found"


def test_add_grade_rejects_empty_name(client: TestClient) -> None:
    headers = _auth(client)
    client.post("/api/v1/curriculum/boards", json={"name": "ICSE"}, headers=headers)
    res = client.post(
        "/api/v1/curriculum/grades",
        json={"board": "ICSE", "grade": "   "},
        headers=headers,
    )
    assert res.status_code == 400
    assert "required" in res.json()["detail"].lower()
