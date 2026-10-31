"""A small client for the backend API. The frontend holds no business rules: everything it shows
comes from these calls, and the server decides what each user may see and do.

The client keeps the login tokens in memory for one browser session. When the short-lived access
token expires it quietly swaps the refresh token for a new pair and retries once.
"""

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"


class ApiError(Exception):
    """The backend refused or failed a request. `detail` is its short machine-readable reason."""

    def __init__(self, status: int, detail: str, retry_after: int | None = None) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail
        self.retry_after = retry_after


class ApiUnreachable(Exception):
    """The backend could not be reached at all."""


class SessionExpired(Exception):
    """The login ended (refresh token expired or revoked). The user has to sign in again."""


def base_url() -> str:
    return os.environ.get("API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


class ApiClient:
    def __init__(self, url: str | None = None, client: httpx.Client | None = None) -> None:
        self._url = (url or base_url()).rstrip("/")
        # Answering can take a while (retrieval, then the language model), hence the long timeout.
        self._http = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=5.0))
        self._access: str | None = None
        self._refresh: str | None = None
        self.user: dict[str, Any] | None = None

    # ------------------------------------------------------------------ session

    @property
    def is_logged_in(self) -> bool:
        return self._access is not None

    def _store(self, pair: dict[str, Any]) -> None:
        self._access = pair["access_token"]
        self._refresh = pair["refresh_token"]
        self.user = pair["user"]

    def _forget(self) -> None:
        self._access = self._refresh = self.user = None

    def login(self, login_id: str, password: str) -> dict[str, Any]:
        """`login_id` may be a Login ID or an email address."""
        pair = self._send("POST", "/auth/login", json={"login_id": login_id, "password": password})
        self._store(pair)
        assert self.user is not None
        return self.user

    def setup_status(self) -> dict[str, Any]:
        """Does the system still need its first Super Admin? Needs no login."""
        status: dict[str, Any] = self._send("GET", "/auth/setup")
        return status

    def setup(self, email: str, full_name: str, password: str, token: str = "") -> None:
        """Create the first Super Admin. Does not sign in; call `login` afterwards."""
        self._send(
            "POST",
            "/auth/setup",
            json={
                "email": email,
                "full_name": full_name,
                "password": password,
                "setup_token": token,
            },
        )

    def logout(self) -> None:
        refresh, self._refresh = self._refresh, None
        try:
            if refresh:
                self._send("POST", "/auth/logout", json={"refresh_token": refresh})
        except (ApiError, ApiUnreachable):
            pass  # the local session ends either way
        finally:
            self._forget()

    def _renew(self) -> bool:
        if not self._refresh:
            return False
        try:
            pair = self._send("POST", "/auth/refresh", json={"refresh_token": self._refresh})
        except ApiError:
            return False
        self._store(pair)
        return True

    # ------------------------------------------------------------------ requests

    def _send(self, method: str, path: str, **kwargs: Any) -> Any:
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
            retry = response.headers.get("retry-after", "")
            raise ApiError(response.status_code, detail, int(retry) if retry.isdigit() else None)
        return response.json() if response.content else None

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """A request as the logged-in user. One automatic renewal if the token has expired."""
        if not self._access:
            raise SessionExpired
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._access}"}
            try:
                return self._send(method, path, headers=headers, **kwargs)
            except ApiError as error:
                if error.status != 401:
                    raise
                if attempt == 2 or not self._renew():
                    self._forget()
                    raise SessionExpired from error
        raise SessionExpired  # not reached

    # ------------------------------------------------------------------ health and documents

    def healthy(self) -> bool:
        try:
            return bool(self._send("GET", "/health")["status"] == "ok")
        except (ApiUnreachable, ApiError):
            return False

    def list_documents(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/documents"))

    def upload(
        self,
        files: list[tuple[str, bytes]],
        scope: str,
        department: str | None,
        company_wide: bool = False,
    ) -> list[dict[str, Any]]:
        data = {"scope": scope}
        if department:
            data["department"] = department
        if scope == "enterprise":
            data["visibility"] = "company" if company_wide else "department"
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

    # ------------------------------------------------------------------ chat

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

    # ------------------------------------------------------------------ users (Super Admin)

    def list_users(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/users"))

    def create_user(
        self, email: str, full_name: str, password: str, role: str, departments: list[str]
    ) -> dict[str, Any]:
        body = {
            "email": email,
            "full_name": full_name,
            "password": password,
            "role": role,
            "departments": departments,
        }
        return dict(self._request("POST", "/users", json=body))

    def update_user(self, user_id: str, **changes: Any) -> dict[str, Any]:
        return dict(self._request("PATCH", f"/users/{user_id}", json=changes))

    def reset_password(self, user_id: str, password: str) -> None:
        self._request("POST", f"/users/{user_id}/password", json={"password": password})
