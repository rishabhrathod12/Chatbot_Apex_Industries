import logging
import time

from app.rag.generation.grounding import check_citations, source_from_hit
from app.rag.generation.llm import ChatModel
from app.rag.generation.overview import answer_overview, is_overview_question
from app.rag.generation.prompts import NOT_FOUND_MESSAGE, NOT_FOUND_TOKEN, build_messages
from app.rag.generation.rerank import rerank
from app.rag.ingestion.embedder import Embedder
from app.rag.retrieval import RetrievalMode, retrieve_detailed
from app.rag.vectorstore import SearchFilter, SearchHit, VectorStore
from app.schemas.chat import ChatAnswer

logger = logging.getLogger("app.rag")

# The relevance check's top score ("directly answers"), required of a passage that is below the
# similarity floor and was let through only because keyword search ranked it highly.
RESCUE_MIN_RELEVANCE = 3


def _not_found(reason: str) -> ChatAnswer:
    return ChatAnswer(status="not_found", answer=NOT_FOUND_MESSAGE, sources=[], reason=reason)


def answer_question(
    question: str,
    *,
    embedder: Embedder,
    store: VectorStore,
    chat_model: ChatModel,
    search_filter: SearchFilter | None = None,
    top_k: int = 5,
    min_score: float = 0.40,
    retrieval_mode: RetrievalMode = "semantic",
    candidates: int = 20,
    max_scan: int = 5000,
    use_rerank: bool = False,
    rerank_top_n: int = 8,
    rerank_min_relevance: int = 2,
    keyword_rescue: int = 0,
    overview: bool = False,
    overview_max_documents: int = 6,
    overview_max_chars: int = 36000,
    overview_max_answer_tokens: int = 3000,
) -> ChatAnswer:
    """Retrieve, optionally re-check relevance, ask the model, and check the answer.

    The model only ever sees chunks the filter allowed, that scored at least `min_score` (or, when
    reranking is on, that keyword search ranked in its top `keyword_rescue`), and (when reranking
    is on) that a relevance check kept. If nothing qualifies the answering model is not
    called at all. An answer is only returned when it cites at least one excerpt that was provided.
    """
    if overview and is_overview_question(question):
        # A request about a whole document has no best-matching passages, so it is answered from
        # excerpts spread across the document(s), through the same permission filter.
        return answer_overview(
            question,
            store=store,
            chat_model=chat_model,
            search_filter=search_filter,
            max_documents=overview_max_documents,
            max_chars=overview_max_chars,
            max_answer_tokens=overview_max_answer_tokens,
            max_scan=max_scan,
        )
    started = time.perf_counter()
    wanted = max(top_k, rerank_top_n) if use_rerank else top_k
    found = retrieve_detailed(
        embedder,
        store,
        question,
        top_k=wanted,
        search_filter=search_filter,
        mode=retrieval_mode,
        candidates=candidates,
        max_scan=max_scan,
        keyword_ranks=keyword_rescue,
    )
    hits = found.hits
    # Meaning-similarity can miss a line buried in a long passage even when the words match
    # exactly. A strong keyword match may still reach the relevance check, which decides.
    rescued = found.keyword_top if use_rerank else frozenset()
    relevant = [hit for hit in hits if hit.score >= min_score or hit.chunk_id in rescued]

    reranked = dropped = 0
    low_relevance = False
    if use_rerank and relevant:
        outcome = rerank(
            question, relevant, chat_model, top_n=rerank_top_n, min_relevance=rerank_min_relevance
        )
        if outcome is not None:
            reranked, dropped = len(relevant[:rerank_top_n]), outcome.dropped
            # A passage that only got in through the keyword rescue must be judged to answer the
            # question directly, not just to contain related facts.
            relevant = [
                hit
                for hit in outcome.hits
                if hit.score >= min_score
                or outcome.scores.get(hit.chunk_id, 0) >= RESCUE_MIN_RELEVANCE
            ]
            low_relevance = not relevant
        else:
            # No judge was available: the rescue must not skip the similarity floor unchecked.
            relevant = [hit for hit in relevant if hit.score >= min_score]
    relevant = relevant[:top_k]

    if relevant:
        result = _generate(question, relevant, chat_model)
    else:
        result = _not_found("low_relevance" if low_relevance else "no_relevant_context")
    logger.info(
        "question answered",
        extra={
            "status": result.status,
            "reason": result.reason,
            "mode": retrieval_mode,
            "retrieved": len(hits),
            "relevant": len(relevant),
            "reranked": reranked,
            "dropped_by_rerank": dropped,
            "sources": len(result.sources),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )
    return result


def _generate(question: str, relevant: list[SearchHit], chat_model: ChatModel) -> ChatAnswer:
    raw = chat_model.complete(build_messages(question, relevant)).strip()
    if not raw:
        return _not_found("empty_answer")
    if raw.upper().startswith(NOT_FOUND_TOKEN):
        return _not_found("model_declined")

    text, used = check_citations(raw, len(relevant))
    if not used:
        # An answer that points at none of the provided excerpts can't be checked, so drop it.
        return _not_found("uncited_answer")
    return ChatAnswer(
        status="answered",
        answer=text,
        sources=[source_from_hit(number, relevant[number - 1], text) for number in used],
    )
