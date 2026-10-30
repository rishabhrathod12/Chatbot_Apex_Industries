import hashlib
import logging
import math
import re
from collections.abc import Sequence
from typing import Any, Protocol

import openai

from app.core.config import Settings

logger = logging.getLogger("app.embedder")


class EmbeddingError(Exception):
    """Embedding failed. `reason` is a short machine-readable code (never contains content)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str,
        dimensions: int,
        batch_size: int = 100,
        client: Any = None,
    ) -> None:
        self.model_name = model
        self.dimensions = dimensions
        self._batch_size = max(1, batch_size)
        # The SDK retries rate limits, 5xx and connection errors with backoff.
        self._client = client or openai.OpenAI(api_key=api_key, max_retries=3, timeout=30.0)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            try:
                response = self._client.embeddings.create(model=self.model_name, input=batch)
            except openai.OpenAIError as exc:
                # Log only the error type. Messages can echo request details.
                logger.error("embedding request failed", extra={"error_type": type(exc).__name__})
                raise EmbeddingError("embedding_request_failed") from exc
            items = sorted(response.data, key=lambda item: item.index)
            if len(items) != len(batch):
                raise EmbeddingError("embedding_count_mismatch")
            for item in items:
                if len(item.embedding) != self.dimensions:
                    raise EmbeddingError("embedding_dimension_mismatch")
                vectors.append(list(item.embedding))
        return vectors


_WORD = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Offline bag-of-words embedder: each word is hashed into a fixed number of buckets.

    Texts that share words end up close together, so retrieval plumbing can be tested and run
    without an API key. It has no real semantic understanding. Development and tests only.
    """

    DEFAULT_DIMENSIONS = 256

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self.model_name = f"hashing-{dimensions}"
        self.dimensions = dimensions

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in _WORD.findall(text.lower()):
            digest = hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            vector[value % self.dimensions] += 1.0 if (value >> 63) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector] if norm else vector

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


def embedding_dimensions(settings: Settings) -> int:
    """Vector size for the configured provider, known without creating an embedder or a key."""
    if settings.embedding_provider == "hashing":
        return HashingEmbedder.DEFAULT_DIMENSIONS
    return settings.embedding_dimensions


def get_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "hashing":
        return HashingEmbedder()
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if not key:
        raise EmbeddingError("embedding_not_configured")
    return OpenAIEmbedder(
        api_key=key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
    )
