"""Calls the real OpenAI API. Skipped unless a key is set.

Run with:  pytest -m live

Costs a fraction of a cent. Not part of the normal suite.
"""

import pytest

from app.core.config import Settings
from app.rag.ingestion.embedder import OpenAIEmbedder, get_embedder


def _has_key() -> bool:
    key = Settings().openai_api_key  # same sources as the app: environment, then .env
    return bool(key and key.get_secret_value())


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not _has_key(), reason="OPENAI_API_KEY not set"),
]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / ((sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5))


def test_real_embeddings_have_the_right_size_and_capture_meaning() -> None:
    embedder = get_embedder(Settings())
    assert isinstance(embedder, OpenAIEmbedder)
    question, answer, unrelated = embedder.embed(
        [
            "What should I do if a machine overheats?",
            "Stop the process and escalate over-temperature indications to a technician.",
            "Submit your leave request through the HR system.",
        ]
    )
    assert len(question) == embedder.dimensions == 1536
    assert _cosine(question, answer) > _cosine(question, unrelated)
