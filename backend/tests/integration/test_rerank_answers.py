"""Reranking inside the answering step, with a fake model that plays both the judge and the
answerer (it tells them apart by the system prompt)."""

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_model_provider
from app.core.config import Settings, get_settings
from app.rag.generation import LlmError, answer_question
from app.rag.generation.llm import ChatMessage
from app.rag.generation.prompts import NOT_FOUND_MESSAGE
from tests.conftest import SAMPLE_DOCS, IndexedSamples

QUESTION = "When can I re-enter an evacuated area?"
JUDGE_MARK = "judge how well passages"


def passages_in(message: ChatMessage) -> list[str]:
    """The passage texts inside a reranker prompt, in order."""
    body = message["content"].split("<passages>\n", 1)[1].split("\n</passages>", 1)[0]
    return [p.split("\n", 1)[1] for p in re.split(r"\n\n(?=\[\d+\] )", body)]


@dataclass
class RoutedChat:
    """Answers questions, and judges passages with `judge`."""

    judge: Callable[[list[str]], dict[str, int]] = lambda texts: {
        str(i): 3 for i in range(1, len(texts) + 1)
    }
    answer: str = "Done [1]."
    rerank_reply: str | None = None  # a fixed reply instead of using `judge`
    fail_rerank: bool = False
    judged: list[list[str]] = field(default_factory=list)
    answer_prompts: list[str] = field(default_factory=list)

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        if JUDGE_MARK in messages[0]["content"]:
            if self.fail_rerank:
                raise LlmError("llm_request_failed")
            texts = passages_in(messages[1])
            self.judged.append(texts)
            return self.rerank_reply or json.dumps(self.judge(texts))
        self.answer_prompts.append(messages[1]["content"])
        return self.answer


def ask(samples: IndexedSamples, chat: RoutedChat, **kw: object):  # noqa: ANN202
    options = dict(top_k=3, min_score=0.0, use_rerank=True)
    options.update(kw)
    return answer_question(
        QUESTION,
        embedder=samples.embedder,
        store=samples.store,
        chat_model=chat,
        **options,  # type: ignore[arg-type]
    )


def excerpts_in(prompt: str) -> int:
    return len(re.findall(r"^\[\d+\] ", prompt, re.MULTILINE))


# ---------- what the judge changes ----------


def test_only_passages_the_judge_keeps_reach_the_answer_prompt(
    indexed_samples: IndexedSamples,
) -> None:
    chat = RoutedChat(
        judge=lambda texts: {str(i + 1): 3 if "evacuated" in t else 0 for i, t in enumerate(texts)}
    )
    result = ask(indexed_samples, chat)
    assert result.status == "answered"
    assert len(chat.judged) == 1 and len(chat.judged[0]) > 3  # it saw more than top_k candidates
    assert excerpts_in(chat.answer_prompts[0]) == 1
    assert "evacuated" in chat.answer_prompts[0]
    assert result.sources[0].section_title == "6. Emergency response"


def test_the_judge_sees_more_candidates_than_the_answer_step_uses(
    indexed_samples: IndexedSamples,
) -> None:
    chat = RoutedChat()  # keeps everything
    ask(indexed_samples, chat, top_k=3, rerank_top_n=6)
    assert len(chat.judged[0]) == 6
    assert excerpts_in(chat.answer_prompts[0]) == 3  # then it is cut back to top_k


def test_the_judge_can_reorder_what_the_answer_step_sees(indexed_samples: IndexedSamples) -> None:
    chat = RoutedChat(
        judge=lambda texts: {str(i + 1): 3 if i == len(texts) - 1 else 2 for i in range(len(texts))}
    )
    ask(indexed_samples, chat, top_k=3, rerank_top_n=5)
    last_candidate = chat.judged[0][-1]
    first_excerpt = chat.answer_prompts[0].split("<context>\n[1] ", 1)[1]
    assert last_candidate.split("\n")[0] in first_excerpt  # the best-scored one is excerpt 1


def test_when_the_judge_drops_everything_the_answer_step_is_never_called(
    indexed_samples: IndexedSamples,
) -> None:
    chat = RoutedChat(judge=lambda texts: {str(i + 1): 1 for i in range(len(texts))})
    result = ask(indexed_samples, chat)
    assert (result.status, result.reason, result.answer) == (
        "not_found",
        "low_relevance",
        NOT_FOUND_MESSAGE,
    )
    assert chat.answer_prompts == []


def test_the_minimum_relevance_can_be_changed(indexed_samples: IndexedSamples) -> None:
    chat = RoutedChat(judge=lambda texts: {str(i + 1): 1 for i in range(len(texts))})
    result = ask(indexed_samples, chat, rerank_min_relevance=1)
    assert result.status == "answered"


# ---------- when the judge cannot be used ----------


@pytest.mark.parametrize("reply", ["I think passage 2 is best", '{"1": 3}', '{"1": 99}'])
def test_an_unusable_judgement_falls_back_to_the_retrieval_order(
    indexed_samples: IndexedSamples, reply: str
) -> None:
    chat = RoutedChat(rerank_reply=reply)
    result = ask(indexed_samples, chat)
    assert result.status == "answered"
    assert excerpts_in(chat.answer_prompts[0]) == 3


def test_a_failing_judge_does_not_stop_answers(indexed_samples: IndexedSamples) -> None:
    chat = RoutedChat(fail_rerank=True)
    assert ask(indexed_samples, chat).status == "answered"
    assert len(chat.answer_prompts) == 1


# ---------- when it is not used ----------


def test_without_reranking_the_judge_is_never_asked(indexed_samples: IndexedSamples) -> None:
    chat = RoutedChat()
    result = ask(indexed_samples, chat, use_rerank=False)
    assert result.status == "answered"
    assert chat.judged == []


def test_if_the_relevance_floor_removes_everything_the_judge_is_not_asked(
    indexed_samples: IndexedSamples,
) -> None:
    chat = RoutedChat()
    result = ask(indexed_samples, chat, min_score=0.99)
    assert result.reason == "no_relevant_context"
    assert chat.judged == [] and chat.answer_prompts == []


def test_hybrid_retrieval_runs_through_the_answering_step(indexed_samples: IndexedSamples) -> None:
    chat = RoutedChat()
    result = ask(indexed_samples, chat, retrieval_mode="hybrid", use_rerank=False)
    assert result.status == "answered"
    assert result.sources


# ---------- the optional keyword rescue of the similarity floor ----------

FLOOR = 0.99  # nothing reaches this by meaning, so only the rescue can reach the judge


def rescue_ask(samples: IndexedSamples, chat: RoutedChat, **kw: object):  # noqa: ANN202
    return ask(samples, chat, retrieval_mode="hybrid", min_score=FLOOR, **kw)


def test_retrieval_reports_which_hits_keyword_search_ranked_highest(
    indexed_samples: IndexedSamples,
) -> None:
    from app.rag.retrieval import retrieve_detailed

    found = retrieve_detailed(
        indexed_samples.embedder,
        indexed_samples.store,
        QUESTION,
        top_k=5,
        mode="hybrid",
        keyword_ranks=2,
    )
    assert len(found.keyword_top) == 2
    assert found.keyword_top <= {hit.chunk_id for hit in found.hits} | found.keyword_top
    plain = retrieve_detailed(
        indexed_samples.embedder, indexed_samples.store, QUESTION, top_k=5, mode="semantic"
    )
    assert plain.keyword_top == frozenset()


def test_without_the_rescue_a_hybrid_search_still_stops_at_the_floor(
    indexed_samples: IndexedSamples,
) -> None:
    chat = RoutedChat()
    result = rescue_ask(indexed_samples, chat)  # rescue defaults to off
    assert result.reason == "no_relevant_context"
    assert chat.judged == [] and chat.answer_prompts == []


def test_a_rescued_passage_must_be_judged_to_answer_directly(
    indexed_samples: IndexedSamples,
) -> None:
    chat = RoutedChat()  # the judge scores everything 3
    result = rescue_ask(indexed_samples, chat, keyword_rescue=2)
    assert result.status == "answered"
    assert len(chat.judged) == 1 and len(chat.judged[0]) == 2  # only the rescued ones were judged


def test_a_rescued_passage_judged_only_partly_relevant_is_not_used(
    indexed_samples: IndexedSamples,
) -> None:
    chat = RoutedChat(judge=lambda texts: {str(i + 1): 2 for i in range(len(texts))})
    result = rescue_ask(indexed_samples, chat, keyword_rescue=2)
    assert (result.status, result.reason) == ("not_found", "low_relevance")
    assert chat.answer_prompts == []


def test_a_rescued_passage_is_dropped_when_the_judge_cannot_be_used(
    indexed_samples: IndexedSamples,
) -> None:
    chat = RoutedChat(fail_rerank=True)
    result = rescue_ask(indexed_samples, chat, keyword_rescue=2)
    assert result.reason == "no_relevant_context"
    assert chat.answer_prompts == []


def test_the_rescue_needs_reranking_to_be_on(indexed_samples: IndexedSamples) -> None:
    chat = RoutedChat()
    result = rescue_ask(indexed_samples, chat, keyword_rescue=2, use_rerank=False)
    assert result.reason == "no_relevant_context"
    assert chat.judged == [] and chat.answer_prompts == []


# ---------- through the API ----------


@pytest.fixture
def api(client: TestClient, settings: Settings) -> tuple[TestClient, RoutedChat]:
    on = settings.model_copy(
        update={"retrieval_mode": "hybrid", "rerank_enabled": True, "retrieval_min_score": 0.0}
    )
    client.app.dependency_overrides[get_settings] = lambda: on  # type: ignore[attr-defined]
    chat = RoutedChat()
    client.app.dependency_overrides[get_chat_model_provider] = lambda: lambda: chat  # type: ignore[attr-defined]
    pdf = (SAMPLE_DOCS / "03_Factory_Safety_Manual.pdf").read_bytes()
    response = client.post(
        "/api/v1/documents",
        data={"scope": "enterprise", "department": "safety", "visibility": "company"},
        files=[("files", ("safety.pdf", pdf))],
    )
    assert response.status_code == 201
    return client, chat


def test_chat_endpoint_uses_the_configured_retrieval_and_reranking(
    api: tuple[TestClient, RoutedChat],
) -> None:
    client, chat = api
    body = client.post("/api/v1/chat", json={"question": QUESTION}).json()
    assert body["status"] == "answered"
    assert body["sources"][0]["doc_code"] == "APM-HSE-MAN-003"
    assert len(chat.judged) == 1


def test_search_endpoint_uses_hybrid_retrieval_for_exact_ids(
    api: tuple[TestClient, RoutedChat],
) -> None:
    client, _ = api
    hits = client.post("/api/v1/search", json={"query": "APM-HSE-MAN-003", "top_k": 3}).json()
    assert hits[0]["doc_code"] == "APM-HSE-MAN-003"
