import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.rag.ingestion.embedder import Embedder
from app.rag.retrieval.lexical import bm25_search
from app.rag.retrieval.semantic import semantic_search
from app.rag.vectorstore import SearchFilter, SearchHit, VectorStore

RetrievalMode = Literal["semantic", "hybrid"]
RRF_K = 60  # the usual constant: how quickly lower ranks stop mattering


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[str]:
    """Merge several best-first lists of ids into one. An id near the top of any list scores well,
    and one that appears in several lists scores best. Only ranks are used, so scores from
    different methods (similarity vs BM25) never need to be compared."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    # Equal scores are broken by id so the order is always the same.
    return [cid for cid, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


@dataclass(frozen=True)
class Retrieval:
    """The best chunks for a question, and which of them keyword search ranked near the top."""

    hits: list[SearchHit]
    keyword_top: frozenset[str] = field(default_factory=frozenset)  # chunk ids, best first ranks


def retrieve(
    embedder: Embedder,
    store: VectorStore,
    query: str,
    *,
    top_k: int,
    search_filter: SearchFilter | None = None,
    mode: RetrievalMode = "semantic",
    candidates: int = 20,
    max_scan: int = 5000,
) -> list[SearchHit]:
    """Find the best chunks for a question (see `retrieve_detailed`)."""
    return retrieve_detailed(
        embedder,
        store,
        query,
        top_k=top_k,
        search_filter=search_filter,
        mode=mode,
        candidates=candidates,
        max_scan=max_scan,
    ).hits


def retrieve_detailed(
    embedder: Embedder,
    store: VectorStore,
    query: str,
    *,
    top_k: int,
    search_filter: SearchFilter | None = None,
    mode: RetrievalMode = "semantic",
    candidates: int = 20,
    max_scan: int = 5000,
    keyword_ranks: int = 3,
) -> Retrieval:
    """Find the best chunks for a question.

    `semantic` is vector search only. `hybrid` also runs keyword search over the chunks the filter
    allows and fuses the two rankings. Either way the same filter applies, and every returned hit
    carries its meaning-similarity score so the relevance gate works the same in both modes.
    """
    if mode == "semantic":
        return Retrieval(
            semantic_search(embedder, store, query, top_k=top_k, search_filter=search_filter)
        )

    query_vector = embedder.embed([query])[0]
    semantic = store.query(query_vector, top_k=max(candidates, top_k), search_filter=search_filter)
    allowed = store.scan(search_filter, limit=max_scan)
    lexical = bm25_search(query, allowed, max(candidates, top_k))

    by_id: dict[str, SearchHit] = {hit.chunk_id: hit for hit in semantic}
    missing = [h.chunk.chunk_id for h in lexical if h.chunk.chunk_id not in by_id]
    vectors = store.vectors_for(missing)
    for hit in lexical:
        chunk = hit.chunk
        if chunk.chunk_id in by_id:
            continue
        vector = vectors.get(chunk.chunk_id)
        score = _cosine(query_vector, vector) if vector else 0.0
        by_id[chunk.chunk_id] = SearchHit(chunk.chunk_id, chunk.text, score, chunk.metadata)

    order = reciprocal_rank_fusion(
        [[h.chunk_id for h in semantic], [h.chunk.chunk_id for h in lexical]]
    )
    keyword_top = frozenset(h.chunk.chunk_id for h in lexical[:keyword_ranks])
    return Retrieval([by_id[chunk_id] for chunk_id in order[:top_k]], keyword_top)
