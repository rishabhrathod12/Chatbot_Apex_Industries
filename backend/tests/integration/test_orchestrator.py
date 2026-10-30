import pytest

from app.rag.generation import LlmError, answer_question
from app.rag.generation.prompts import NOT_FOUND_MESSAGE
from app.rag.retrieval import semantic_search
from app.rag.vectorstore import SearchFilter
from tests.conftest import IndexedSamples, ScriptedChat

QUESTION = "When can I re-enter an evacuated area?"


def _ask(
    samples: IndexedSamples,
    chat: ScriptedChat,
    question: str = QUESTION,
    *,
    min_score: float = 0.0,
    top_k: int = 3,
    search_filter: SearchFilter | None = None,
):  # noqa: ANN202
    return answer_question(
        question,
        embedder=samples.embedder,
        store=samples.store,
        chat_model=chat,
        search_filter=search_filter,
        top_k=top_k,
        min_score=min_score,
    )


def _top_hits(samples: IndexedSamples, question: str = QUESTION, top_k: int = 3):  # noqa: ANN202
    return semantic_search(samples.embedder, samples.store, question, top_k=top_k)


def test_cited_answer_comes_back_with_sources_built_from_metadata(
    indexed_samples: IndexedSamples,
) -> None:
    hits = _top_hits(indexed_samples)
    chat = ScriptedChat("Do not go back in until the incident controller says so [1].")
    result = _ask(indexed_samples, chat)

    assert result.status == "answered"
    assert result.reason is None
    assert result.answer == "Do not go back in until the incident controller says so [1]."
    assert len(result.sources) == 1
    source = result.sources[0]
    assert source.number == 1
    assert source.chunk_id == hits[0].chunk_id
    assert source.doc_code == hits[0].metadata["doc_code"]
    assert source.section_title == hits[0].metadata["section_title"]
    assert source.page_start == hits[0].metadata["page_start"]


def test_model_receives_system_rules_and_only_the_retrieved_excerpts(
    indexed_samples: IndexedSamples,
) -> None:
    hits = _top_hits(indexed_samples)
    chat = ScriptedChat("ok [1]")
    _ask(indexed_samples, chat)
    assert len(chat.calls) == 1
    system, user = chat.calls[0]
    assert system["role"] == "system"
    assert user["role"] == "user"
    assert hits[0].text in user["content"]
    assert QUESTION in user["content"]
    assert user["content"].count("\n[") >= 2  # several numbered excerpts


def test_weak_matches_never_reach_the_model(indexed_samples: IndexedSamples) -> None:
    chat = ScriptedChat("should not be called")
    result = _ask(indexed_samples, chat, min_score=0.99)
    assert result.status == "not_found"
    assert result.reason == "no_relevant_context"
    assert result.answer == NOT_FOUND_MESSAGE
    assert result.sources == []
    assert chat.calls == []


@pytest.mark.parametrize("reply", ["NOT_FOUND", "not_found", "NOT_FOUND.", "  NOT_FOUND\n"])
def test_model_can_decline(indexed_samples: IndexedSamples, reply: str) -> None:
    result = _ask(indexed_samples, ScriptedChat(reply))
    assert (result.status, result.reason) == ("not_found", "model_declined")
    assert result.answer == NOT_FOUND_MESSAGE


def test_answer_without_any_citation_is_discarded(indexed_samples: IndexedSamples) -> None:
    result = _ask(indexed_samples, ScriptedChat("You can walk back in whenever you like."))
    assert (result.status, result.reason) == ("not_found", "uncited_answer")
    assert "walk back in" not in result.answer  # the unverifiable text is not passed on
    assert result.sources == []


def test_answer_citing_only_invented_excerpts_is_discarded(
    indexed_samples: IndexedSamples,
) -> None:
    result = _ask(indexed_samples, ScriptedChat("Go right in [7]."))
    assert (result.status, result.reason) == ("not_found", "uncited_answer")


def test_invalid_markers_are_removed_and_valid_ones_kept(indexed_samples: IndexedSamples) -> None:
    result = _ask(indexed_samples, ScriptedChat("Wait for the all-clear [1] and then enter [9]."))
    assert result.status == "answered"
    assert result.answer == "Wait for the all-clear [1] and then enter."
    assert [s.number for s in result.sources] == [1]


def test_grouped_markers_are_cleaned_individually(indexed_samples: IndexedSamples) -> None:
    result = _ask(indexed_samples, ScriptedChat("Both apply [1, 9, 2]."))
    assert result.answer == "Both apply [1, 2]."
    assert [s.number for s in result.sources] == [1, 2]


def test_sources_are_listed_once_in_order_of_first_use(indexed_samples: IndexedSamples) -> None:
    hits = _top_hits(indexed_samples)
    result = _ask(indexed_samples, ScriptedChat("First B [2], then A [1], then B again [2]."))
    assert [s.number for s in result.sources] == [2, 1]
    assert [s.chunk_id for s in result.sources] == [hits[1].chunk_id, hits[0].chunk_id]


def test_empty_reply_is_not_found(indexed_samples: IndexedSamples) -> None:
    result = _ask(indexed_samples, ScriptedChat("   \n"))
    assert (result.status, result.reason) == ("not_found", "empty_answer")


def test_permission_filter_keeps_other_departments_out_of_the_prompt(
    indexed_samples: IndexedSamples,
) -> None:
    chat = ScriptedChat("From HR [1].")
    _ask(indexed_samples, chat, search_filter=SearchFilter(departments=["hr"]), top_k=5)
    prompt = chat.calls[0][1]["content"]
    assert "APM-HR-POL-005" in prompt
    assert "APM-HSE-MAN-003" not in prompt
    assert "Emergency response" not in prompt


def test_department_with_no_documents_gets_no_answer_and_no_model_call(
    indexed_samples: IndexedSamples,
) -> None:
    chat = ScriptedChat("should not be called")
    result = _ask(
        indexed_samples, chat, "goods receipt", search_filter=SearchFilter(departments=["stores"])
    )
    assert result.status == "not_found"
    assert chat.calls == []


def test_model_failure_is_raised_not_swallowed(indexed_samples: IndexedSamples) -> None:
    chat = ScriptedChat(error=LlmError("llm_request_failed"))
    with pytest.raises(LlmError) as exc:
        _ask(indexed_samples, chat)
    assert exc.value.reason == "llm_request_failed"
