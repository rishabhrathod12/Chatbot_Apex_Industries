from pathlib import Path

from fastapi.testclient import TestClient
from httpx import Response

from app.core.config import Settings
from tests.conftest import make_docx

URL = "/api/v1/documents"


def _upload(
    client: TestClient,
    files: list[tuple[str, bytes]],
    scope: str = "enterprise",
    department: str | None = "production",
) -> Response:
    data = {"scope": scope}
    if department is not None:
        data["department"] = department
    return client.post(URL, data=data, files=[("files", (n, c)) for n, c in files])


def _stored(settings: Settings) -> list[Path]:
    d = Path(settings.upload_dir)
    return [p for p in d.iterdir() if not p.name.startswith(".tmp-")] if d.exists() else []


def test_upload_valid_pdf_stores_file_and_record(
    client: TestClient, settings: Settings, sample_pdf: bytes
) -> None:
    r = _upload(client, [("manual.pdf", sample_pdf)])
    assert r.status_code == 201
    result = r.json()["results"][0]
    assert result["status"] == "accepted"
    doc = result["document"]
    assert doc["department"] == "production"
    assert doc["original_filename"] == "manual.pdf"
    stored = _stored(settings)
    assert len(stored) == 1
    assert stored[0].name != "manual.pdf"
    assert stored[0].read_bytes() == sample_pdf
    assert client.get(f"{URL}/{doc['id']}").status_code == 200


def test_duplicate_detected_and_not_stored_twice(
    client: TestClient, settings: Settings, sample_pdf: bytes
) -> None:
    first = _upload(client, [("a.pdf", sample_pdf)]).json()["results"][0]
    second = _upload(client, [("renamed.pdf", sample_pdf)])
    result = second.json()["results"][0]
    assert second.status_code == 200
    assert result["status"] == "duplicate"
    assert result["document"]["id"] == first["document"]["id"]
    assert len(_stored(settings)) == 1
    assert len(client.get(URL).json()) == 1


def test_same_file_in_other_department_is_not_duplicate(
    client: TestClient, sample_pdf: bytes
) -> None:
    _upload(client, [("a.pdf", sample_pdf)], department="production")
    r = _upload(client, [("a.pdf", sample_pdf)], department="safety")
    assert r.json()["results"][0]["status"] == "accepted"


def test_mixed_batch_gives_per_file_results(client: TestClient, sample_pdf: bytes) -> None:
    r = _upload(
        client,
        [("good.pdf", sample_pdf), ("evil.pdf", b"MZ fake exe"), ("notes.txt", b"hello")],
    )
    assert r.status_code == 201
    results = {x["filename"]: x for x in r.json()["results"]}
    assert results["good.pdf"]["status"] == "accepted"
    assert results["evil.pdf"] == {
        "filename": "evil.pdf",
        "status": "rejected",
        "reason": "content_mismatch",
        "document": None,
    }
    assert results["notes.txt"]["status"] == "accepted"


def test_docx_accepted(client: TestClient) -> None:
    r = _upload(client, [("policy.docx", make_docx())], scope="personal", department=None)
    assert r.status_code == 201
    assert r.json()["results"][0]["document"]["department"] == ""


def test_unsupported_extension_rejected(client: TestClient) -> None:
    r = _upload(client, [("tool.exe", b"MZ")])
    assert r.status_code == 422
    assert r.json()["results"][0]["reason"] == "unsupported_type"


def test_empty_file_rejected(client: TestClient) -> None:
    r = _upload(client, [("empty.txt", b"")])
    assert r.json()["results"][0]["reason"] == "empty_file"


def test_oversize_returns_413_and_leaves_nothing_on_disk(
    client: TestClient, settings: Settings
) -> None:
    big = b"a" * (settings.max_upload_mb * 1024 * 1024 + 1)
    r = _upload(client, [("big.txt", big)])
    assert r.status_code == 413
    assert r.json()["results"][0]["reason"] == "file_too_large"
    assert list(Path(settings.upload_dir).iterdir()) == []


def test_path_traversal_filename_cannot_escape_upload_dir(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    r = _upload(client, [("../../escape.txt", b"hello")])
    result = r.json()["results"][0]
    assert result["document"]["original_filename"] == "escape.txt"
    assert not (tmp_path.parent / "escape.txt").exists()
    assert not (tmp_path / "escape.txt").exists()
    stored = _stored(settings)
    assert len(stored) == 1
    assert stored[0].parent == Path(settings.upload_dir)


def test_too_many_files_rejected(client: TestClient) -> None:
    r = _upload(client, [(f"{i}.txt", f"x{i}".encode()) for i in range(4)])
    assert r.status_code == 400


def test_enterprise_requires_valid_department(client: TestClient) -> None:
    assert _upload(client, [("a.txt", b"x")], department=None).status_code == 422
    assert _upload(client, [("a.txt", b"x")], department="finance").status_code == 422


def test_department_alias_normalised(client: TestClient) -> None:
    r = _upload(client, [("a.txt", b"x")], department="Quality Control")
    assert r.json()["results"][0]["document"]["department"] == "quality"


def test_delete_removes_file_and_allows_reupload(
    client: TestClient, settings: Settings, sample_pdf: bytes
) -> None:
    doc_id = _upload(client, [("a.pdf", sample_pdf)]).json()["results"][0]["document"]["id"]
    assert client.delete(f"{URL}/{doc_id}").status_code == 204
    assert _stored(settings) == []
    assert client.get(f"{URL}/{doc_id}").status_code == 404
    assert client.get(URL).json() == []
    again = _upload(client, [("a.pdf", sample_pdf)])
    assert again.json()["results"][0]["status"] == "accepted"


def test_get_and_delete_unknown_id_404(client: TestClient) -> None:
    assert client.get(f"{URL}/nope").status_code == 404
    assert client.delete(f"{URL}/nope").status_code == 404
