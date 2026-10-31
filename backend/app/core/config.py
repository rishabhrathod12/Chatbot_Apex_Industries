from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to the repo root so the same .env is found whichever folder the server starts in.
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def normalize_database_url(url: str) -> str:
    """Accept the usual PostgreSQL spellings and pin them to the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    openai_api_key: SecretStr | None = None
    jwt_secret_key: SecretStr | None = None

    # Login and tokens. Access tokens are short; refresh tokens let a session continue.
    jwt_issuer: str = "apex-knowledge-assistant"
    access_token_minutes: int = 30
    refresh_token_hours: int = 8
    login_max_failures: int = 5  # wrong passwords allowed per email+address within the window
    login_window_minutes: int = 15
    password_min_length: int = 12
    password_hash_cost_log2: int = 15  # scrypt cost; tests lower it to stay fast

    database_url: str = "sqlite:///./storage/app.db"
    vector_store_path: str = "./vector_db"
    upload_dir: str = "./storage/uploads"
    max_upload_mb: int = 20
    max_files_per_request: int = 10
    max_pdf_pages: int = 200
    chunk_max_tokens: int = 600
    chunk_overlap_tokens: int = 80

    embedding_provider: Literal["openai", "hashing"] = (
        "openai"  # "hashing" = offline, dev/test only
    )
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    embedding_batch_size: int = 100

    # Default model name is a placeholder to be confirmed against what the account can use.
    chat_model: str = "gpt-4.1-mini"
    chat_temperature: float | None = 0.0  # None = leave to the model (some models require it)
    chat_max_tokens: int = 600
    retrieval_top_k: int = 5
    # How candidates are found, and whether a model re-checks them before they are used.
    retrieval_mode: Literal["semantic", "hybrid"] = "hybrid"  # hybrid = meaning + keywords
    retrieval_candidates: int = 20  # candidates gathered per method before fusing
    lexical_max_chunks: int = 5000  # keyword search scans at most this many permitted chunks
    rerank_enabled: bool = True
    rerank_top_n: int = 8  # candidates the reranking model is shown
    rerank_min_relevance: int = 2  # 0-3 scale; lower-scored candidates are dropped
    chat_history_turns: int = 3  # earlier question/answer pairs used to resolve follow-ups
    # Chunks scoring below this meaning-similarity are never shown to the model. 0.25 is a floor for
    # use WITH reranking, which judges relevance properly. Without reranking the right value is
    # about 0.40 (see docs/retrieval-baseline.md; a looser gate then lets weak matches through).
    retrieval_min_score: float = 0.25

    @model_validator(mode="after")
    def _check_database_url(self) -> "Settings":
        self.database_url = normalize_database_url(self.database_url)
        if not self.database_url.startswith(("sqlite:///", "postgresql+psycopg://")):
            raise ValueError("DATABASE_URL must start with sqlite:/// or postgresql://")
        return self

    @model_validator(mode="after")
    def _require_secrets_in_production(self) -> "Settings":
        if self.app_env == "production" and not (
            self.jwt_secret_key and self.jwt_secret_key.get_secret_value()
        ):
            raise ValueError("JWT_SECRET_KEY must be set when APP_ENV=production")
        return self

    @model_validator(mode="after")
    def _check_auth_settings(self) -> "Settings":
        secret = self.jwt_secret_key.get_secret_value() if self.jwt_secret_key else ""
        if secret and len(secret) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        if not 1 <= self.access_token_minutes <= 240:
            raise ValueError("ACCESS_TOKEN_MINUTES must be between 1 and 240")
        if not 1 <= self.refresh_token_hours <= 24 * 30:
            raise ValueError("REFRESH_TOKEN_HOURS must be between 1 and 720")
        if self.login_max_failures < 1 or self.login_window_minutes < 1:
            raise ValueError("LOGIN_MAX_FAILURES and LOGIN_WINDOW_MINUTES must be at least 1")
        if self.password_min_length < 8:
            raise ValueError("PASSWORD_MIN_LENGTH must be at least 8")
        if not 10 <= self.password_hash_cost_log2 <= 20:
            raise ValueError("PASSWORD_HASH_COST_LOG2 must be between 10 and 20")
        return self

    @model_validator(mode="after")
    def _no_hashing_embedder_in_production(self) -> "Settings":
        if self.app_env == "production" and self.embedding_provider == "hashing":
            raise ValueError("EMBEDDING_PROVIDER=hashing is for development and tests only")
        return self

    @model_validator(mode="after")
    def _check_generation_settings(self) -> "Settings":
        if not 0.0 <= self.retrieval_min_score <= 1.0:
            raise ValueError("RETRIEVAL_MIN_SCORE must be between 0 and 1")
        if not 0 <= self.chat_history_turns <= 10:
            raise ValueError("CHAT_HISTORY_TURNS must be between 0 and 10")
        if not 1 <= self.retrieval_candidates <= 100:
            raise ValueError("RETRIEVAL_CANDIDATES must be between 1 and 100")
        if not 1 <= self.rerank_top_n <= 20:
            raise ValueError("RERANK_TOP_N must be between 1 and 20")
        if not 0 <= self.rerank_min_relevance <= 3:
            raise ValueError("RERANK_MIN_RELEVANCE must be between 0 and 3")
        if self.lexical_max_chunks < 1:
            raise ValueError("LEXICAL_MAX_CHUNKS must be at least 1")
        if not 1 <= self.retrieval_top_k <= 20:
            raise ValueError("RETRIEVAL_TOP_K must be between 1 and 20")
        if self.chat_temperature is not None and not 0.0 <= self.chat_temperature <= 2.0:
            raise ValueError("CHAT_TEMPERATURE must be between 0 and 2")
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
