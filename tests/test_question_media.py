"""Question image upload/serve — files persist in the database."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.academic import QuestionMediaFile
from tests.conftest import login

PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x00\x03\x00\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _tutor_auth(client: TestClient) -> dict[str, str]:
    token = login(client, "tutor@test.edu", "TEST")
    return {"Authorization": f"Bearer {token}"}


def test_upload_and_get_question_media(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.services.question_media.settings.question_media_root", str(tmp_path))
    headers = _tutor_auth(client)
    upload = client.post(
        "/api/v1/question-media",
        headers=headers,
        files={"file": ("monitor.png", PNG_1X1, "image/png")},
    )
    assert upload.status_code == 200, upload.text
    body = upload.json()
    key = body["key"]
    url = body["url"]
    assert key.startswith("inst-1/")
    assert url == f"/question-media/{key}"

    fetched = client.get(f"/api/v1{url}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.headers["content-type"].startswith("image/png")
    assert fetched.content == PNG_1X1


def test_question_media_survives_missing_disk_file(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("app.services.question_media.settings.question_media_root", str(tmp_path))
    headers = _tutor_auth(client)
    upload = client.post(
        "/api/v1/question-media",
        headers=headers,
        files={"file": ("option.png", PNG_1X1, "image/png")},
    )
    key = upload.json()["key"]
    disk_file = tmp_path / key
    if disk_file.exists():
        disk_file.unlink()

    row = db.get(QuestionMediaFile, key)
    assert row is not None
    assert bytes(row.data) == PNG_1X1

    fetched = client.get(f"/api/v1/question-media/{key}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.content == PNG_1X1
