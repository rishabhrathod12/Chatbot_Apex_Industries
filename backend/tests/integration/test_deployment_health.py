from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import health
from app.core.config import Settings


def test_ready_reports_ok_and_creates_the_storage_folders(
    client: TestClient, settings: Settings
) -> None:
    assert not Path(settings.upload_dir).exists()  # a fresh install has neither folder yet
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "storage": "ok"}}
    assert Path(settings.upload_dir).is_dir() and Path(settings.vector_store_path).is_dir()


def test_ready_says_unavailable_when_the_database_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "_database_ok", lambda _s: False)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "failed", "storage": "ok"}


def test_ready_says_unavailable_when_storage_is_not_writable(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    settings.upload_dir = str(blocker / "uploads")  # a folder can't be made inside a file
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["storage"] == "failed"


def test_ready_never_leaks_error_details(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_s: Settings) -> bool:
        raise RuntimeError("password=hunter2 host=db.internal")

    monkeypatch.setattr(health, "_storage_ok", boom)
    response = client.get("/api/v1/health/ready")
    assert "hunter2" not in response.text


def test_responses_carry_security_headers(client: TestClient) -> None:
    headers = client.get("/api/v1/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_development_api_pages_keep_working_without_the_strict_policy(
    client: TestClient,
) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers
    assert response.headers["x-frame-options"] == "DENY"
