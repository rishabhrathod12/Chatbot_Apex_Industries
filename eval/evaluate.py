"""Run the golden set against a store and compute retrieval metrics."""

from dataclasses import dataclass, field

from app.rag.ingestion.embedder import Embedder
from app.rag.retrieval import semantic_search
from app.rag.vectorstore import VectorStore
from eval.metrics import Case, RetrievedChunk, first_hit_rank, score_spread, summarize


@dataclass
class RetrievalReport:
    by_document: dict[str, float]
    by_page: dict[str, float]
    score_spread: dict[str, float]
    misses: list[str] = field(default_factory=list)  # ids of answerable cases not found in top-k


def _page(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _retrieved(
    embedder: Embedder, store: VectorStore, case: Case, top_k: int
) -> list[RetrievedChunk]:
    hits = semantic_search(embedder, store, case.question, top_k=top_k)
    return [
        RetrievedChunk(
            doc_code=str(h.metadata.get("doc_code", "")),
            page_start=_page(h.metadata.get("page_start")),
            page_end=_page(h.metadata.get("page_end")),
            score=h.score,
        )
        for h in hits
    ]


def evaluate_retrieval(
    cases: list[Case], embedder: Embedder, store: VectorStore, top_k: int = 5
) -> RetrievalReport:
    doc_ranks: list[int | None] = []
    page_ranks: list[int | None] = []
    answerable_top: list[float] = []
    unanswerable_top: list[float] = []
    misses: list[str] = []

    for case in cases:
        chunks = _retrieved(embedder, store, case, top_k)
        top_score = chunks[0].score if chunks else 0.0
        if not case.answerable:
            unanswerable_top.append(top_score)
            continue
        answerable_top.append(top_score)
        doc_rank = first_hit_rank(chunks, case.expected, check_page=False)
        doc_ranks.append(doc_rank)
        page_ranks.append(first_hit_rank(chunks, case.expected, check_page=True))
        if doc_rank is None:
            misses.append(case.id)

    return RetrievalReport(
        by_document=summarize(doc_ranks),
        by_page=summarize(page_ranks),
        score_spread=score_spread(answerable_top, unanswerable_top),
        misses=misses,
    )
