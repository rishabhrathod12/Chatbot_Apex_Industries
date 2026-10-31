"""No endpoint may be reachable without logging in, unless it is on the short public list.

The test calls every route of the real app with no credentials, so a new endpoint added without
authentication fails here automatically.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app import main
from app.core.config import Settings

PUBLIC = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/health/ready"),  # for Docker/load balancers; says ok/failed only
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
}


def _routes(client: TestClient) -> list[tuple[str, str]]:
    """Every method and path the app really serves, read from its own API description. (Walking
    `app.routes` is unreliable: newer FastAPI nests included routers instead of flattening them.)"""
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    )


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "0" * 32, path)


def test_every_route_is_either_public_on_purpose_or_requires_a_login(
    raw_client: TestClient,
) -> None:
    routes = _routes(raw_client)
    assert len(routes) >= 19, "the route list looks incomplete"

    unprotected = []
    for method, path in routes:
        if (method, path) in PUBLIC:
            continue
        response = raw_client.request(method, _fill(path))
        if response.status_code != 401:
            unprotected.append(f"{method} {path} -> {response.status_code}")
    assert not unprotected, f"reachable without logging in: {unprotected}"


def test_a_garbage_token_is_no_better_than_no_token(raw_client: TestClient) -> None:
    for method, path in _routes(raw_client):
        if (method, path) in PUBLIC:
            continue
        response = raw_client.request(
            method, _fill(path), headers={"Authorization": "Bearer garbage.token.value"}
        )
        assert response.status_code == 401, f"{method} {path}"


def test_the_public_list_is_exactly_what_we_expect(raw_client: TestClient) -> None:
    routes = set(_routes(raw_client))
    assert PUBLIC <= routes, "a public endpoint disappeared; update the list on purpose"


def test_the_public_endpoints_do_not_leak_anything_secret(raw_client: TestClient) -> None:
    assert raw_client.get("/api/v1/health").json() == {"status": "ok"}


def test_api_documentation_is_not_served_in_production(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        main, "get_settings", lambda: settings.model_copy(update={"app_env": "production"})
    )
    client = TestClient(main.create_app())
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    assert TestClient(main.create_app()).get("/openapi.json").status_code == 200
