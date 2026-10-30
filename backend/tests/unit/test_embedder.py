import math
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.rag.ingestion.embedder import (
    EmbeddingError,
    HashingEmbedder,
    OpenAIEmbedder,
    embedding_dimensions,
    get_embedder,
)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# ---------- HashingEmbedder ----------


def test_hashing_embedder_is_deterministic_and_unit_length() -> None:
    e = HashingEmbedder()
    first = e.embed(["Never bypass guards"])[0]
    assert first == e.embed(["Never bypass guards"])[0]
    assert len(first) == e.dimensions == 256
    assert math.isclose(math.sqrt(sum(x * x for x in first)), 1.0)


def test_hashing_embedder_puts_shared_words_closer() -> None:
    a, b, c = HashingEmbedder().embed(
        [
            "emergency stop devices must never be bypassed",
            "never bypass the emergency stop",
            "submit leave requests through the HR system",
        ]
    )
    assert _cosine(a, b) > _cosine(a, c)


def test_hashing_embedder_handles_text_without_words() -> None:
    assert HashingEmbedder().embed(["--- !!!"])[0] == [0.0] * 256


# ---------- OpenAIEmbedder with a fake client ----------


class _FakeEmbeddings:
    def __init__(self, dimensions: int, shuffle: bool = False, wrong_count: bool = False) -> None:
        self.calls: list[list[str]] = []
        self._dimensions = dimensions
        self._shuffle = shuffle
        self._wrong_count = wrong_count

    def create(self, model: str, input: list[str]) -> Any:
        self.calls.append(list(input))
        items = [
            SimpleNamespace(index=i, embedding=[float(len(text))] + [0.0] * (self._dimensions - 1))
            for i, text in enumerate(input)
        ]
        if self._shuffle:
            items.reverse()
        if self._wrong_count:
            items = items[:-1]
        return SimpleNamespace(data=items)


def _embedder(fake: Any, dimensions: int = 4, batch_size: int = 2) -> OpenAIEmbedder:
    client = SimpleNamespace(embeddings=fake)
    return OpenAIEmbedder("sk-test", "test-model", dimensions, batch_size, client=client)


def test_openai_embedder_batches_and_keeps_order() -> None:
    fake = _FakeEmbeddings(4)
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    vectors = _embedder(fake).embed(texts)
    assert fake.calls == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_openai_embedder_restores_order_when_api_returns_shuffled() -> None:
    vectors = _embedder(_FakeEmbeddings(4, shuffle=True), batch_size=10).embed(["a", "bb", "ccc"])
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0]


def test_openai_embedder_rejects_wrong_dimensions() -> None:
    with pytest.raises(EmbeddingError) as exc:
        _embedder(_FakeEmbeddings(8), dimensions=4).embed(["a"])
    assert exc.value.reason == "embedding_dimension_mismatch"


def test_openai_embedder_rejects_missing_vectors() -> None:
    with pytest.raises(EmbeddingError) as exc:
        _embedder(_FakeEmbeddings(4, wrong_count=True)).embed(["a", "b"])
    assert exc.value.reason == "embedding_count_mismatch"


def test_openai_api_errors_become_embedding_errors_without_leaking_input() -> None:
    class Failing:
        def create(self, model: str, input: list[str]) -> Any:
            raise openai.APIConnectionError(request=httpx.Request("POST", "http://example.invalid"))

    with pytest.raises(EmbeddingError) as exc:
        _embedder(SimpleNamespace(create=Failing().create)).embed(["very secret document text"])
    assert exc.value.reason == "embedding_request_failed"
    assert "secret" not in str(exc.value)


def test_empty_input_makes_no_api_call() -> None:
    fake = _FakeEmbeddings(4)
    assert _embedder(fake).embed([]) == []
    assert fake.calls == []


# ---------- provider selection ----------


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_openai_provider_without_key_is_reported_not_crashed() -> None:
    with pytest.raises(EmbeddingError) as exc:
        get_embedder(_settings(embedding_provider="openai"))
    assert exc.value.reason == "embedding_not_configured"


def test_provider_selection_and_dimensions() -> None:
    assert isinstance(get_embedder(_settings(embedding_provider="hashing")), HashingEmbedder)
    with_key = get_embedder(_settings(openai_api_key="sk-test"))
    assert isinstance(with_key, OpenAIEmbedder)
    assert with_key.model_name == "text-embedding-3-small"
    assert embedding_dimensions(_settings()) == 1536
    assert embedding_dimensions(_settings(embedding_provider="hashing")) == 256


def test_hashing_embedder_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="development and tests only"):
        _settings(app_env="production", jwt_secret_key="x" * 32, embedding_provider="hashing")
