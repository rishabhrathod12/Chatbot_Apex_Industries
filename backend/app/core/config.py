from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    openai_api_key: SecretStr | None = None
    jwt_secret_key: SecretStr | None = None

    database_url: str = "sqlite:///./storage/app.db"
    chroma_path: str = "./chroma_db"
    upload_dir: str = "./storage/uploads"
    max_upload_mb: int = 20
    max_files_per_request: int = 10
    max_pdf_pages: int = 200
    chunk_max_tokens: int = 600
    chunk_overlap_tokens: int = 80

    @model_validator(mode="after")
    def _require_secrets_in_production(self) -> "Settings":
        if self.app_env == "production" and not (
            self.jwt_secret_key and self.jwt_secret_key.get_secret_value()
        ):
            raise ValueError("JWT_SECRET_KEY must be set when APP_ENV=production")
        return self

    @model_validator(mode="after")
    def _check_chunk_sizes(self) -> "Settings":
        if self.chunk_max_tokens < 50:
            raise ValueError("CHUNK_MAX_TOKENS must be at least 50")
        if not 0 <= self.chunk_overlap_tokens < self.chunk_max_tokens // 2:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be >= 0 and below half of CHUNK_MAX_TOKENS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
