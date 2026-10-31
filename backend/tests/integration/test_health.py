from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.api.health import API_LEVEL
from app.main import create_app


def test_health_returns_ok_with_request_id() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "api_level": API_LEVEL}
    assert response.headers["X-Request-ID"]


def test_unhandled_error_returns_generic_500() -> None:
    app = create_app()
    router = APIRouter()

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    app.include_router(router)
    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_server_error"
    assert "secret internal detail" not in response.text
    assert body["request_id"] == response.headers["X-Request-ID"]
