"""A small client for the backend API. The frontend holds no secrets and no business rules:
everything it shows comes from these calls."""

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"


class ApiError(Exception):
    """The backend refused or failed a request. `detail` is its short machine-readable reason."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class ApiUnreachable(Exception):
    """The backend could not be reached at all."""


def base_url() -> str:
    return os.environ.get("API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


class ApiClient:
    def __init__(self, url: str | None = None, client: httpx.Client | None = None) -> None:
        self._url = (url or base_url()).rstrip("/")
        # Answering can take a while (retrieval, then the language model), hence the long timeout.
        self._http = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=5.0))

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, f"{self._url}{path}", **kwargs)
        except httpx.TransportError as exc:
            raise ApiUnreachable(str(exc)) from exc
        if response.status_code >= 400:
            detail = "unknown_error"
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("detail"), str):
                    detail = body["detail"]
                elif isinstance(body, dict) and "results" in body:
                    return body  # an upload where every file was rejected still has a useful body
            except ValueError:
                pass
            raise ApiError(response.status_code, detail)
        return response.json() if response.content else None

    # ---- health and documents ----

    def healthy(self) -> bool:
        try:
            return bool(self._request("GET", "/health")["status"] == "ok")
        except (ApiUnreachable, ApiError):
            return False

    def list_documents(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/documents"))

    def upload(
        self, files: list[tuple[str, bytes]], scope: str, department: str | None
    ) -> list[dict[str, Any]]:
        data = {"scope": scope}
        if department:
            data["department"] = department
        body = self._request(
            "POST",
            "/documents",
            data=data,
            files=[("files", (name, content)) for name, content in files],
        )
        return list(body["results"])

    def reindex(self, document_id: str) -> None:
        self._request("POST", f"/documents/{document_id}/reindex")

    def delete_document(self, document_id: str) -> None:
        self._request("DELETE", f"/documents/{document_id}")

    # ---- chat ----

    def chat(
        self,
        question: str,
        scope: str,
        department: str | None,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"question": question, "scope": scope}
        if department:
            payload["department"] = department
        if conversation_id:
            payload["conversation_id"] = conversation_id
        return dict(self._request("POST", "/chat", json=payload))

    def list_conversations(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/conversations"))

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/conversations/{conversation_id}"))

    def delete_conversation(self, conversation_id: str) -> None:
        self._request("DELETE", f"/conversations/{conversation_id}")
