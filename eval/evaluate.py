"""Run the golden set against a store and compute retrieval metrics."""

from dataclasses import dataclass, field

from app.rag.generation import ChatModel, answer_question
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
        if case.not_specified:
            continue  # the topic IS in the documents, so a high similarity is legitimate
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


# ---------- end-to-end answers (needs a chat model) ----------


@dataclass(frozen=True)
class AnswerResult:
    case_id: str
    case_type: str
    question: str
    status: str
    reason: str | None
    answer: str
    source_codes: tuple[str, ...]
    expected_codes: tuple[str, ...]

    @property
    def cited_expected_source(self) -> bool:
        return bool(set(self.source_codes) & set(self.expected_codes))


def evaluate_answers(
    cases: list[Case],
    embedder: Embedder,
    store: VectorStore,
    chat_model: ChatModel,
    *,
    top_k: int = 5,
    min_score: float = 0.40,
) -> list[AnswerResult]:
    results: list[AnswerResult] = []
    for case in cases:
        reply = answer_question(
            case.question,
            embedder=embedder,
            store=store,
            chat_model=chat_model,
            top_k=top_k,
            min_score=min_score,
        )
        results.append(
            AnswerResult(
                case_id=case.id,
                case_type=case.type,
                question=case.question,
                status=reply.status,
                reason=reply.reason,
                answer=reply.answer,
                source_codes=tuple(dict.fromkeys(s.doc_code for s in reply.sources)),
                expected_codes=tuple(dict.fromkeys(e.doc_code for e in case.expected)),
            )
        )
    return results


def summarize_answers(results: list[AnswerResult]) -> dict[str, object]:
    answerable = [r for r in results if r.expected_codes]
    unanswerable = [r for r in results if r.case_type == "unanswerable"]
    not_specified = [r for r in results if r.case_type == "not_specified"]
    answered = [r for r in answerable if r.status == "answered"]
    return {
        "answerable": len(answerable),
        "answerable_answered": len(answered),
        "answerable_cited_expected_source": sum(r.cited_expected_source for r in answered),
        "false_refusals": [r.case_id for r in answerable if r.status != "answered"],
        "wrong_source": [r.case_id for r in answered if not r.cited_expected_source],
        "unanswerable": len(unanswerable),
        "unanswerable_refused": sum(r.status == "not_found" for r in unanswerable),
        "answered_but_should_refuse": [r.case_id for r in unanswerable if r.status == "answered"],
        "not_specified": len(not_specified),
        "not_specified_refused": sum(r.status == "not_found" for r in not_specified),
        "not_specified_answered_review_manually": [
            r.case_id for r in not_specified if r.status == "answered"
        ],
        "refusal_reasons": sorted(
            {r.reason or "" for r in unanswerable if r.status == "not_found"}
        ),
    }
