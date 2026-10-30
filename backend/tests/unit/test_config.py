import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def test_defaults_are_valid_for_development() -> None:
    s = _settings()
    assert s.app_env == "development"
    assert s.openai_api_key is None


def test_invalid_environment_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(app_env="staging")


def test_production_requires_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        _settings(app_env="production")


def test_production_accepts_jwt_secret() -> None:
    s = _settings(app_env="production", jwt_secret_key="x" * 32)
    assert s.jwt_secret_key is not None


def test_secrets_not_exposed_in_repr() -> None:
    s = _settings(openai_api_key="sk-supersecretvalue123")
    assert "supersecretvalue123" not in repr(s)
