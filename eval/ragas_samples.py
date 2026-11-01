"""Collect what RAGAS needs to judge: the question, the answer, and the excerpts the model saw.

Nothing here imports ragas, so it runs (and is tested) in the normal environment.

The excerpts are read back from the prompt that was really sent to the answering model, so they
are exactly what the model had, whatever retrieval and reranking settings were in use.
"""

import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.rag.generation import answer_question
from app.rag.generation.llm import ChatMessage, ChatModel
from app.rag.generation.prompts import SYSTEM_PROMPT
from app.rag.ingestion.embedder import Embedder
from app.rag.retrieval import RetrievalMode
from app.rag.vectorstore import VectorStore
from eval.metrics import Case

_CITATION = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")


@dataclass(frozen=True)
class RagasSample:
    case_id: str
    case_type: str
    tag: str
    question: str
    answer: str  # citation markers like [1] removed, they are not part of the meaning
    contexts: tuple[str, ...]


@dataclass(frozen=True)
class SkippedCase:
    """No answer was given (a refusal), so there is nothing for RAGAS to judge."""

    case_id: str
    case_type: str
    reason: str


def parse_contexts(user_content: str) -> list[str]:
    """Split the `<context>` block of an answering prompt back into its numbered excerpts.

    Each excerpt starts with a label line (document, section, page) that is dropped, leaving
    only the passage text. Excerpts are found in order ([1], [2], ...) so a "[3]" inside a
    passage can't be mistaken for a boundary.
    """
    start = user_content.find("<context>\n")
    end = user_content.rfind("\n</context>")
    if start == -1 or end == -1 or end < start:
        return []
    body = user_content[start + len("<context>\n") : end]
    excerpts: list[str] = []
    position = 0
    number = 1
    while body.startswith(f"[{number}] ", position):
        following = body.find(f"\n\n[{number + 1}] ", position)
        stop = following if following != -1 else len(body)
        _label, _, text = body[position:stop].partition("\n")
        excerpts.append(text.strip())
        if following == -1:
            break
        position = following + 2
        number += 1
    return excerpts


def strip_citations(answer: str) -> str:
    return _CITATION.sub("", answer).strip()


class RecordingChat:
    """Passes every call through and remembers the excerpts of the answering prompt."""

    def __init__(self, inner: ChatModel) -> None:
        self._inner = inner
        self.contexts: list[str] = []

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        if messages and messages[0]["content"] == SYSTEM_PROMPT and len(messages) > 1:
            self.contexts = parse_contexts(messages[1]["content"])
        return self._inner.complete(messages)


def collect_samples(
    cases: list[Case],
    embedder: Embedder,
    store: VectorStore,
    chat_model: ChatModel,
    *,
    top_k: int = 5,
    min_score: float = 0.25,
    retrieval_mode: RetrievalMode = "hybrid",
    use_rerank: bool = True,
    rerank_top_n: int = 8,
    rerank_min_relevance: int = 2,
    keyword_rescue: int = 0,
    workers: int = 1,
) -> tuple[list[RagasSample], list[SkippedCase]]:
    """Answer every case the way the app does and keep the ones that got an answer."""

    def one(case: Case) -> RagasSample | SkippedCase:
        recorder = RecordingChat(chat_model)
        reply = answer_question(
            case.question,
            embedder=embedder,
            store=store,
            chat_model=recorder,
            top_k=top_k,
            min_score=min_score,
            retrieval_mode=retrieval_mode,
            use_rerank=use_rerank,
            rerank_top_n=rerank_top_n,
            rerank_min_relevance=rerank_min_relevance,
            keyword_rescue=keyword_rescue,
        )
        if reply.status != "answered" or not recorder.contexts:
            return SkippedCase(case.id, case.type, reply.reason or reply.status)
        return RagasSample(
            case_id=case.id,
            case_type=case.type,
            tag=case.tag,
            question=case.question,
            answer=strip_citations(reply.answer),
            contexts=tuple(recorder.contexts),
        )

    if workers <= 1:
        outcomes = [one(case) for case in cases]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(one, cases))
    samples = [o for o in outcomes if isinstance(o, RagasSample)]
    skipped = [o for o in outcomes if isinstance(o, SkippedCase)]
    return samples, skipped
