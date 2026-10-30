import logging
import re
import time

from app.rag.generation.llm import ChatModel
from app.rag.generation.prompts import NOT_FOUND_MESSAGE, NOT_FOUND_TOKEN, build_messages
from app.rag.ingestion.embedder import Embedder
from app.rag.retrieval import semantic_search
from app.rag.vectorstore import SearchFilter, SearchHit, VectorStore
from app.schemas.chat import ChatAnswer, Source

logger = logging.getLogger("app.rag")

_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def _not_found(reason: str) -> ChatAnswer:
    return ChatAnswer(status="not_found", answer=NOT_FOUND_MESSAGE, sources=[], reason=reason)


def _check_citations(text: str, available: int) -> tuple[str, list[int]]:
    """Keep only markers that point at an excerpt we really sent. Returns cleaned text and the
    excerpt numbers used, in order of first appearance."""
    used: list[int] = []

    def keep_valid(match: re.Match[str]) -> str:
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        valid = [n for n in numbers if 1 <= n <= available]
        for n in valid:
            if n not in used:
                used.append(n)
        return f"[{', '.join(str(n) for n in valid)}]" if valid else ""

    cleaned = _MARKER.sub(keep_valid, text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    return cleaned.strip(), used


def _source(number: int, hit: SearchHit) -> Source:
    meta = hit.metadata
    start, end = meta.get("page_start"), meta.get("page_end")
    return Source(
        number=number,
        chunk_id=hit.chunk_id,
        doc_code=str(meta.get("doc_code", "")),
        doc_title=str(meta.get("doc_title", "")),
        source_name=str(meta.get("source_name", "")),
        section_title=str(meta.get("section_title", "")),
        page_start=start if isinstance(start, int) else None,
        page_end=end if isinstance(end, int) else None,
    )


def answer_question(
    question: str,
    *,
    embedder: Embedder,
    store: VectorStore,
    chat_model: ChatModel,
    search_filter: SearchFilter | None = None,
    top_k: int = 5,
    min_score: float = 0.25,
) -> ChatAnswer:
    """Retrieve, ask the model, and check the answer before returning it.

    The model only ever sees chunks the filter allowed and that scored at least `min_score`. If
    nothing qualifies the model is not called at all. An answer is only returned when it cites at
    least one excerpt that was actually provided.
    """
    started = time.perf_counter()
    hits = semantic_search(embedder, store, question, top_k=top_k, search_filter=search_filter)
    relevant = [hit for hit in hits if hit.score >= min_score]

    result = (
        _generate(question, relevant, chat_model) if relevant else _not_found("no_relevant_context")
    )
    logger.info(
        "question answered",
        extra={
            "status": result.status,
            "reason": result.reason,
            "retrieved": len(hits),
            "relevant": len(relevant),
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

    text, used = _check_citations(raw, len(relevant))
    if not used:
        # An answer that points at none of the provided excerpts can't be checked, so drop it.
        return _not_found("uncited_answer")
    return ChatAnswer(
        status="answered",
        answer=text,
        sources=[_source(number, relevant[number - 1]) for number in used],
    )
