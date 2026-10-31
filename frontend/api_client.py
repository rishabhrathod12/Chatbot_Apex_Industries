"""A small client for the backend API. The frontend holds no business rules: everything it shows
comes from these calls, and the server decides what each user may see and do.

The client keeps the login tokens in memory for one browser session. When the short-lived access
token expires it quietly swaps the refresh token for a new pair and retries once.
"""

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"

# The API level (reported by GET /health) this page needs. An API that reports less, or nothing,
# is an older copy still running: the page says so instead of failing in confusing ways.
REQUIRED_API_LEVEL = 2


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

    def login(self, login_id: str, password: str, portal: str | None = None) -> dict[str, Any]:
        """`login_id` may be a Login ID or an email address. `portal` ("employee" or "admin") is
        which sign-in page was used; the server only uses it to refuse a mismatch."""
        body: dict[str, Any] = {"login_id": login_id, "password": password}
        if portal:
            body["portal"] = portal
        pair = self._send("POST", "/auth/login", json=body)
        self._store(pair)
        assert self.user is not None
        return self.user

    def change_password(self, current_password: str, new_password: str) -> dict[str, Any]:
        """Choose a new password. The server ends the other sessions and returns a fresh one."""
        pair = self._request(
            "POST",
            "/auth/change-password",
            json={"current_password": current_password, "new_password": new_password},
        )
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
                elif isinstance(body, dict) and isinstance(body.get("detail"), list):
                    # A validation problem (HTTP 422): the API did not understand what we sent,
                    # which in practice means it is an older or newer version than this page.
                    detail = "request_not_understood"
                elif isinstance(body, dict) and "results" in body:
                    return body  # an upload where every file was rejected still has a useful body
            except ValueError:
                pass
            if detail == "unknown_error" and response.status_code >= 500:
                detail = "server_error"
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

    def api_level(self) -> int | None:
        """The API's level, or None if it does not say (an older copy) or cannot be reached."""
        try:
            level = self._send("GET", "/health").get("api_level")
        except (ApiUnreachable, ApiError, AttributeError):
            return None
        return level if isinstance(level, int) else None

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
        details: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """`details` (title, description, document_type, version) are for company documents."""
        data = {"scope": scope}
        if department:
            data["department"] = department
        if scope == "enterprise":
            data["visibility"] = "company" if company_wide else "department"
            data.update({k: v for k, v in (details or {}).items() if v})
        body = self._request(
            "POST",
            "/documents",
            data=data,
            files=[("files", (name, content)) for name, content in files],
        )
        return list(body["results"])

    def archive_document(self, document_id: str) -> None:
        self._request("POST", f"/documents/{document_id}/archive")

    def restore_document(self, document_id: str) -> None:
        self._request("POST", f"/documents/{document_id}/restore")

    def upload_version(
        self, document_id: str, filename: str, content: bytes, details: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Replace a company document with a new file (the old one stays until the new is ready)."""
        data = {k: v for k, v in (details or {}).items() if v}
        return dict(
            self._request(
                "POST",
                f"/documents/{document_id}/versions",
                data=data,
                files={"file": (filename, content)},
            )
        )

    def list_versions(self, document_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/documents/{document_id}/versions"))

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
        self,
        email: str,
        full_name: str,
        password: str | None,
        role: str,
        departments: list[str],
        login_id: str | None = None,
    ) -> dict[str, Any]:
        """`password` None asks the server for a temporary one (returned once as
        `temporary_password`)."""
        body = {
            "email": email,
            "full_name": full_name,
            "password": password,
            "role": role,
            "departments": departments,
            "login_id": login_id,
        }
        return dict(self._request("POST", "/users", json=body))

    def update_user(self, user_id: str, **changes: Any) -> dict[str, Any]:
        return dict(self._request("PATCH", f"/users/{user_id}", json=changes))

    def reset_password(self, user_id: str, password: str | None) -> str | None:
        """Returns the generated temporary password, or None if `password` was given."""
        result = self._request("POST", f"/users/{user_id}/password", json={"password": password})
        temporary = result.get("temporary_password") if isinstance(result, dict) else None
        return str(temporary) if temporary else None

    # ------------------------------------------------------------------ admin dashboard

    def admin_stats(self) -> dict[str, Any]:
        return dict(self._request("GET", "/admin/stats"))

    def admin_audit(self, page: int = 1, page_size: int = 25, **filters: str) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        params.update({k: v for k, v in filters.items() if v})
        return dict(self._request("GET", "/admin/audit", params=params))
