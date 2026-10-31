import json
import sys
from pathlib import Path

import pytest

from app.rag.ingestion.extractors import extract
from eval.evaluate import (
    AnswerResult,
    FollowupResult,
    evaluate_answers,
    evaluate_followups,
    evaluate_retrieval,
    summarize_answers,
)
from eval.metrics import (
    Case,
    Expected,
    RetrievedChunk,
    first_hit_rank,
    load_followups,
    load_golden,
    score_spread,
    summarize,
)
from eval.run import main
from tests.conftest import SAMPLE_DOCS, IndexedSamples, ScriptedChat


def _chunk(
    code: str, start: int | None = 2, end: int | None = 2, score: float = 0.5
) -> RetrievedChunk:
    return RetrievedChunk(code, start, end, score)


# ---------- metric maths ----------


def test_first_hit_rank_finds_the_first_matching_chunk() -> None:
    chunks = [_chunk("A"), _chunk("B"), _chunk("C")]
    assert first_hit_rank(chunks, [Expected("B", 2)], check_page=True) == 2
    assert first_hit_rank(chunks, [Expected("Z", 2)], check_page=True) is None
    assert first_hit_rank(chunks, [Expected("A", 2), Expected("C", 2)], check_page=False) == 1


def test_page_level_matching_uses_the_chunks_page_range() -> None:
    chunks = [_chunk("A", 1, 1), _chunk("A", 2, 3)]
    assert first_hit_rank(chunks, [Expected("A", 3)], check_page=True) == 2
    assert first_hit_rank(chunks, [Expected("A", 3)], check_page=False) == 1
    assert first_hit_rank([_chunk("A", None, None)], [Expected("A", 2)], check_page=True) == 1


def test_summarize_hit_rates_and_mrr() -> None:
    result = summarize([1, None, 3])
    assert result["hit@1"] == pytest.approx(1 / 3)
    assert result["hit@3"] == pytest.approx(2 / 3)
    assert result["hit@5"] == pytest.approx(2 / 3)
    assert result["mrr"] == pytest.approx((1 + 1 / 3) / 3)
    assert result["n"] == 3
    assert summarize([]) == {}


def test_score_spread() -> None:
    spread = score_spread([0.3, 0.5, 0.7], [0.1, 0.4])
    assert spread == {
        "answerable_min": 0.3,
        "answerable_median": 0.5,
        "unanswerable_max": 0.4,
        "unanswerable_median": pytest.approx(0.25),
    }
    assert score_spread([], []) == {}


# ---------- the golden set itself ----------


def test_golden_set_is_well_formed() -> None:
    cases = load_golden()
    assert len({c.id for c in cases}) == len(cases)
    assert {c.type for c in cases} == {
        "answerable",
        "multi_document",
        "exact_id",
        "unanswerable",
        "not_specified",
    }
    for case in cases:
        assert case.question.strip()
        if case.answerable:
            assert case.expected, case.id
            assert all(e.page in {1, 2, 3} for e in case.expected), case.id
        else:
            assert case.expected == (), case.id


def test_the_m12_questions_are_tagged_and_the_held_out_batches_are_kept_apart() -> None:
    cases = load_golden()
    by_tag: dict[str, int] = {}
    for case in cases:
        by_tag[case.tag] = by_tag.get(case.tag, 0) + 1
    assert by_tag == {
        "": 26,
        "keyword": 10,
        "paraphrase": 8,
        "near_miss": 5,
        "heldout": 11,
        "heldout2": 11,
    }
    assert len(cases) == 71
    # every kind of question has both something to find and something that must be refused
    near = [c for c in cases if c.tag == "near_miss"]
    assert {c.type for c in near} == {"not_specified", "unanswerable"}
    for tag in ("heldout", "heldout2"):
        kinds = {c.type for c in cases if c.tag == tag}
        assert "answerable" in kinds and "unanswerable" in kinds


def test_golden_set_only_points_at_documents_that_exist() -> None:
    real_codes = {extract(pdf, ".pdf").metadata.doc_code for pdf in SAMPLE_DOCS.glob("*.pdf")}
    expected_codes = {e.doc_code for c in load_golden() for e in c.expected}
    assert len(real_codes) == 6
    assert expected_codes <= real_codes


def test_load_golden_reads_a_custom_file(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "X",
                        "type": "answerable",
                        "question": "q",
                        "expected": [{"doc_code": "D"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert load_golden(path) == [Case("X", "answerable", "q", (Expected("D", None),))]


# ---------- evaluation against the real sample index ----------


def test_offline_baseline_has_not_regressed(indexed_samples: IndexedSamples) -> None:
    """Plumbing baseline with the word-matching embedder (measured 2026-10-31: doc hit@5 = 0.95,
    mrr = 0.85, one miss). It guards against pipeline breakage, not retrieval quality."""
    original = [
        c for c in load_golden() if not c.tag
    ]  # the harder M12 questions need real embeddings
    report = evaluate_retrieval(original, indexed_samples.embedder, indexed_samples.store, top_k=5)
    assert report.by_document["n"] == 20
    assert report.by_document["hit@5"] >= 0.90
    assert report.by_document["mrr"] >= 0.80
    assert report.by_page["hit@5"] >= 0.85
    assert len(report.misses) <= 2
    assert {"answerable_min", "unanswerable_max"} <= set(report.score_spread)


def test_runner_refuses_cleanly_without_an_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["eval.run", "--provider", "openai"])
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert main() == 2
    assert "embedding_not_configured" in capsys.readouterr().err


# ---------- the three kinds of "no answer" ----------


def test_not_specified_is_a_separate_kind_from_unanswerable() -> None:
    cases = {c.id: c for c in load_golden()}
    assert cases["D03"].not_specified
    assert not cases["D03"].refusal_expected
    assert cases["D05"].refusal_expected
    assert not cases["D05"].not_specified
    assert not cases["D03"].answerable


def _result(
    case_id: str,
    kind: str,
    status: str,
    sources: tuple[str, ...] = (),
    expected: tuple[str, ...] = (),
) -> AnswerResult:
    return AnswerResult(
        case_id,
        kind,
        "q",
        "",
        status,
        None if status == "answered" else "model_declined",
        "a",
        sources,
        expected,
    )


def test_summarize_answers_counts_each_outcome_separately() -> None:
    summary = summarize_answers(
        [
            _result("A1", "answerable", "answered", ("X",), ("X",)),
            _result("A2", "answerable", "answered", ("Y",), ("X",)),  # cited the wrong document
            _result("A3", "answerable", "not_found", (), ("X",)),  # refused a valid question
            _result("D1", "unanswerable", "not_found"),
            _result("D2", "unanswerable", "answered", ("X",)),  # should have refused
            _result("N1", "not_specified", "not_found"),
            _result("N2", "not_specified", "answered", ("X",)),  # needs a human look
        ]
    )
    assert summary["answerable"] == 3
    assert summary["answerable_answered"] == 2
    assert summary["answerable_cited_expected_source"] == 1
    assert summary["false_refusals"] == ["A3"]
    assert summary["wrong_source"] == ["A2"]
    assert summary["unanswerable"] == 2
    assert summary["unanswerable_refused"] == 1
    assert summary["answered_but_should_refuse"] == ["D2"]
    assert summary["not_specified"] == 2
    assert summary["not_specified_refused"] == 1
    assert summary["not_specified_answered_review_manually"] == ["N2"]


def test_evaluate_answers_runs_every_case_with_a_scripted_model(
    indexed_samples: IndexedSamples,
) -> None:
    cases = load_golden()
    chat = ScriptedChat("Answer from the first excerpt [1].")
    results = evaluate_answers(
        cases, indexed_samples.embedder, indexed_samples.store, chat, top_k=3, min_score=0.0
    )
    assert [r.case_id for r in results] == [c.id for c in cases]
    assert all(r.status == "answered" for r in results)
    assert len(chat.calls) == len(cases)


def test_evaluate_answers_skips_the_model_when_nothing_is_relevant(
    indexed_samples: IndexedSamples,
) -> None:
    chat = ScriptedChat("unused")
    results = evaluate_answers(
        load_golden(), indexed_samples.embedder, indexed_samples.store, chat, min_score=0.99
    )
    assert {r.status for r in results} == {"not_found"}
    assert {r.reason for r in results} == {"no_relevant_context"}
    assert chat.calls == []


# ---------- follow-ups ----------


def test_followup_cases_are_well_formed() -> None:
    followups = load_followups()
    assert [f.id for f in followups] == ["F01", "F02", "F03"]
    for f in followups:
        assert f.first_question.strip() and f.question.strip()
        assert f.expected
    real_codes = {extract(p, ".pdf").metadata.doc_code for p in SAMPLE_DOCS.glob("*.pdf")}
    assert {e.doc_code for f in followups for e in f.expected} <= real_codes


def test_followup_is_resolved_only_when_answered_from_the_right_document() -> None:
    def result(status: str, sources: tuple[str, ...]) -> FollowupResult:
        return FollowupResult("F", "q", None, status, sources, ("X",))

    assert result("answered", ("X", "Y")).resolved
    assert not result("answered", ("Y",)).resolved
    assert not result("not_found", ()).resolved


def test_evaluate_followups_rewrites_before_answering(indexed_samples: IndexedSamples) -> None:
    followups = load_followups()[:1]
    # Call order: answer to the first question, rewrite, answer to the rewritten question.
    chat = ScriptedChat(
        [
            "Check the guards [1].",
            "What must I check before shutting down a CNC machine?",
            "Done [1].",
        ]
    )
    results = evaluate_followups(
        followups, indexed_samples.embedder, indexed_samples.store, chat, min_score=0.0
    )
    assert len(chat.calls) == 3
    assert results[0].rewritten == "What must I check before shutting down a CNC machine?"
    assert "Latest message: And what about shutdown?" in chat.calls[1][1]["content"]
    assert "Question: What must I check before shutting down" in chat.calls[2][1]["content"]
