from typing import Any

from fastapi.testclient import TestClient

from app.api.deps import get_store_provider
from app.core.config import Settings, get_settings
from app.rag.ingestion.pipeline import Scope
from app.rag.vectorstore import VectorStore
from tests.conftest import SAMPLE_DOCS

DOCS = "/api/v1/documents"
SEARCH = "/api/v1/search"
SAFETY = (SAMPLE_DOCS / "03_Factory_Safety_Manual.pdf").read_bytes()
HR = (SAMPLE_DOCS / "05_HR_Policy.pdf").read_bytes()


def _upload(
    client: TestClient, name: str, content: bytes, scope: str = "enterprise", dept: str = "safety"
) -> dict[str, Any]:
    data = {"scope": scope}
    if scope == "enterprise":
        data["department"] = dept
    response = client.post(DOCS, data=data, files=[("files", (name, content))])
    assert response.status_code == 201, response.text
    return response.json()["results"][0]["document"]  # type: ignore[no-any-return]


def _search(client: TestClient, query: str, **kw: Any) -> list[dict[str, Any]]:
    response = client.post(SEARCH, json={"query": query, **kw})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_upload_is_indexed_in_the_background(client: TestClient) -> None:
    uploaded = _upload(client, "safety.pdf", SAFETY)
    assert uploaded["status"] == "uploaded"  # the response is sent before ingestion runs

    doc = client.get(f"{DOCS}/{uploaded['id']}").json()
    assert doc["status"] == "ready"
    assert doc["chunk_count"] == 9
    assert doc["page_count"] == 3
    assert doc["doc_code"] == "APM-HSE-MAN-003"
    assert doc["title"] == "Factory Health & Safety Manual"
    assert doc["status_reason"] is None


def test_search_returns_cited_chunks(client: TestClient) -> None:
    _upload(client, "safety.pdf", SAFETY)
    hits = _search(client, "never bypass guards interlocks emergency stop", top_k=3)
    top = hits[0]
    assert top["doc_code"] == "APM-HSE-MAN-003"
    assert top["section_title"] == "3. Machine and work-area safety"
    assert (top["page_start"], top["page_end"]) == (2, 2)
    assert top["source_name"] == "safety.pdf"
    assert "bypass guards" in top["text"]


def test_search_department_filter(client: TestClient) -> None:
    _upload(client, "safety.pdf", SAFETY, dept="safety")
    _upload(client, "hr.pdf", HR, dept="hr")
    only_safety = _search(client, "leave request", department="safety", top_k=10)
    assert only_safety
    assert {h["department"] for h in only_safety} == {"safety"}
    assert client.post(SEARCH, json={"query": "x", "department": "finance"}).status_code == 422


def test_personal_and_enterprise_searches_are_separate(client: TestClient) -> None:
    _upload(client, "mine.pdf", HR, scope="personal")
    assert _search(client, "leave request", scope="enterprise") == []
    personal = _search(client, "leave request", scope="personal")
    assert personal
    assert personal[0]["doc_code"] == "APM-HR-POL-005"


def test_deleting_a_document_removes_it_from_search(client: TestClient) -> None:
    doc = _upload(client, "safety.pdf", SAFETY)
    assert _search(client, "emergency stop")
    assert client.delete(f"{DOCS}/{doc['id']}").status_code == 204
    assert _search(client, "emergency stop") == []


def test_failed_vector_delete_keeps_the_document_and_reports_an_error(
    client: TestClient, settings: Settings
) -> None:
    doc = _upload(client, "safety.pdf", SAFETY)

    class Failing:
        def delete_document(self, document_id: str) -> None:
            raise RuntimeError("store unavailable")

    def provider(scope: Scope, dimensions: int) -> VectorStore:
        return Failing()  # type: ignore[return-value]

    client.app.dependency_overrides[get_store_provider] = lambda: provider  # type: ignore[attr-defined]
    response = client.delete(f"{DOCS}/{doc['id']}")
    assert response.status_code == 500
    assert response.json() == {"detail": "vector_delete_failed"}
    assert client.get(f"{DOCS}/{doc['id']}").status_code == 200  # still there, not half-deleted


def test_missing_api_key_fails_ingestion_but_not_the_upload(
    client: TestClient, settings: Settings
) -> None:
    no_key = settings.model_copy(update={"embedding_provider": "openai", "openai_api_key": None})
    client.app.dependency_overrides[get_settings] = lambda: no_key  # type: ignore[attr-defined]

    uploaded = _upload(client, "safety.pdf", SAFETY)
    doc = client.get(f"{DOCS}/{uploaded['id']}").json()
    assert doc["status"] == "failed"
    assert doc["status_reason"] == "embedding_not_configured"
    assert doc["chunk_count"] == 0

    response = client.post(SEARCH, json={"query": "emergency stop"})
    assert response.status_code == 503
    assert response.json() == {"detail": "embedding_not_configured"}


def test_reindex_recovers_a_failed_document(client: TestClient, settings: Settings) -> None:
    no_key = settings.model_copy(update={"embedding_provider": "openai", "openai_api_key": None})
    client.app.dependency_overrides[get_settings] = lambda: no_key  # type: ignore[attr-defined]
    uploaded = _upload(client, "safety.pdf", SAFETY)
    assert client.get(f"{DOCS}/{uploaded['id']}").json()["status"] == "failed"

    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    assert client.post(f"{DOCS}/{uploaded['id']}/reindex").status_code == 202
    doc = client.get(f"{DOCS}/{uploaded['id']}").json()
    assert (doc["status"], doc["chunk_count"]) == ("ready", 9)
    assert _search(client, "emergency stop")


def test_reindex_does_not_duplicate_and_unknown_id_is_404(client: TestClient) -> None:
    doc = _upload(client, "safety.pdf", SAFETY)
    client.post(f"{DOCS}/{doc['id']}/reindex")
    client.post(f"{DOCS}/{doc['id']}/reindex")
    hits = _search(client, "emergency stop", top_k=20)
    ids = [h["chunk_id"] for h in hits]
    assert len(ids) == len(set(ids))
    assert client.post(f"{DOCS}/nope/reindex").status_code == 404


def test_a_corrupt_pdf_is_accepted_then_marked_failed(client: TestClient) -> None:
    # Right header, so it passes the upload check, but it can't be read as a PDF.
    doc = _upload(client, "broken.pdf", b"%PDF-1.7\nnot a real pdf")
    final = client.get(f"{DOCS}/{doc['id']}").json()
    assert (final["status"], final["status_reason"]) == ("failed", "corrupt_pdf")
    assert _search(client, "anything") == []


def test_search_validates_its_input(client: TestClient) -> None:
    assert client.post(SEARCH, json={"query": ""}).status_code == 422
    assert client.post(SEARCH, json={"query": "x", "top_k": 0}).status_code == 422
    assert client.post(SEARCH, json={"query": "x", "top_k": 21}).status_code == 422
    assert client.post(SEARCH, json={"query": "x", "scope": "other"}).status_code == 422
