import json
import math
from pathlib import Path

import pytest

import eval.ragas_run as ragas_run
from app.rag.generation.prompts import SYSTEM_PROMPT, build_messages
from app.rag.ingestion.embedder import EmbeddingError
from app.rag.vectorstore import SearchHit
from eval.metrics import Case, load_golden
from eval.ragas_report import (
    ScoredSample,
    clean_score,
    format_report,
    needs_review,
    summarize_scores,
)
from eval.ragas_samples import (
    RagasSample,
    RecordingChat,
    SkippedCase,
    collect_samples,
    parse_contexts,
    strip_citations,
)
from eval.ragas_scoring import RagasNotInstalled, check_ragas
from tests.conftest import IndexedSamples, ScriptedChat


def _hit(text: str, number: int = 1) -> SearchHit:
    return SearchHit(
        chunk_id=f"c{number}",
        score=0.9,
        text=text,
        metadata={"doc_title": "Manual", "doc_code": "APM-1", "page_start": 2, "page_end": 2},
    )


def _sample(case_id: str = "A01", case_type: str = "answerable", tag: str = "") -> RagasSample:
    return RagasSample(case_id, case_type, tag, "q?", "a.", ("ctx",))


def test_parse_contexts_round_trips_what_the_prompt_builder_wrote() -> None:
    hits = [_hit("First passage.\n\nWith a blank line."), _hit("Second, mentions [3] inside.", 2)]
    user = build_messages("What?", hits)[1]["content"]
    assert parse_contexts(user) == [
        "First passage.\n\nWith a blank line.",
        "Second, mentions [3] inside.",
    ]


def test_parse_contexts_without_a_context_block_is_empty() -> None:
    assert parse_contexts("Question: hello") == []


def test_strip_citations_removes_markers_only() -> None:
    assert (
        strip_citations("Check the guard [1]. Then stop [2][3].") == "Check the guard. Then stop."
    )
    assert strip_citations("Limit is 5 [1, 2] bar") == "Limit is 5 bar"


def test_recording_chat_keeps_only_the_answering_prompt() -> None:
    inner = ScriptedChat(replies="ok")
    chat = RecordingChat(inner)
    chat.complete([{"role": "system", "content": "other"}, {"role": "user", "content": "x"}])
    assert chat.contexts == []
    chat.complete(build_messages("q", [_hit("passage")]))
    assert chat.contexts == ["passage"]
    assert len(inner.calls) == 2


def test_collect_samples_keeps_answers_and_skips_refusals(indexed_samples: IndexedSamples) -> None:
    cases = [c for c in load_golden() if c.answerable][:2]
    chat = ScriptedChat(replies=["Do the checks [1].", "NOT_FOUND"])
    samples, skipped = collect_samples(
        cases,
        indexed_samples.embedder,
        indexed_samples.store,
        chat,
        min_score=0.0,
        retrieval_mode="semantic",
        use_rerank=False,
    )
    assert [s.case_id for s in samples] == [cases[0].id]
    assert samples[0].answer == "Do the checks."
    assert samples[0].contexts and all(samples[0].contexts)
    assert skipped == [SkippedCase(cases[1].id, cases[1].type, "model_declined")]


def test_collect_samples_in_parallel_keeps_order(indexed_samples: IndexedSamples) -> None:
    cases: list[Case] = load_golden()[:4]
    samples, skipped = collect_samples(
        cases,
        indexed_samples.embedder,
        indexed_samples.store,
        ScriptedChat(replies="Fine [1]."),
        min_score=0.0,
        retrieval_mode="semantic",
        use_rerank=False,
        workers=3,
    )
    assert [s.case_id for s in samples] + [k.case_id for k in skipped] != []
    assert [s.case_id for s in samples] == [
        c.id for c in cases if c.id not in {k.case_id for k in skipped}
    ]


def test_clean_score_treats_nan_and_junk_as_no_score() -> None:
    assert clean_score(0.5) == 0.5
    assert clean_score(1) == 1.0
    assert clean_score(float("nan")) is None
    assert clean_score(None) is None
    assert clean_score("0.5") is None
    assert clean_score(True) is None


def test_summary_ignores_failed_scores_and_counts_them() -> None:
    scored = [
        ScoredSample(
            _sample("A01"), {"faithfulness": 1.0, "answer_relevancy": 0.8, "context_precision": 1.0}
        ),
        ScoredSample(
            _sample("A02"),
            {"faithfulness": 0.5, "answer_relevancy": None, "context_precision": 0.4},
        ),
    ]
    summary = summarize_scores(scored)
    assert summary["faithfulness"]["mean"] == pytest.approx(0.75)
    assert summary["faithfulness"]["min"] == 0.5
    assert summary["answer_relevancy"]["n"] == 1
    assert summary["answer_relevancy"]["judge_failures"] == 1
    assert needs_review(scored) == {
        "faithfulness": ["A02"],
        "answer_relevancy": ["A02"],  # unscored goes to manual review, never silently passes
        "context_precision": ["A02"],
    }


def test_summary_with_nothing_scored_is_nan_not_a_crash() -> None:
    summary = summarize_scores([])
    assert math.isnan(summary["faithfulness"]["mean"])
    assert "scored 0 answered" in format_report([], [], judge="m", embeddings="e")


def test_ragas_is_not_in_the_app_environment() -> None:
    # The app venv must stay free of ragas (it would downgrade openai). The run script says so.
    try:
        import ragas  # noqa: F401
    except ImportError:
        with pytest.raises(RagasNotInstalled):
            check_ragas()


def test_main_runs_end_to_end_with_a_fake_judge(
    indexed_samples: IndexedSamples,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_score(samples: list[RagasSample], **_: object) -> list[ScoredSample]:
        return [
            ScoredSample(
                s, {"faithfulness": 0.9, "answer_relevancy": 0.8, "context_precision": 0.7}
            )
            for s in samples
        ]

    monkeypatch.setattr(ragas_run, "check_ragas", lambda: None)
    monkeypatch.setattr(ragas_run, "get_embedder", lambda _s: indexed_samples.embedder)
    monkeypatch.setattr(ragas_run, "get_chat_model", lambda _s: ScriptedChat(replies="Yes [1]."))
    monkeypatch.setattr(ragas_run, "score_samples", fake_score)
    monkeypatch.setattr(ragas_run, "index_sample_documents", lambda *_a: indexed_samples.store)
    monkeypatch.setattr(ragas_run, "close_client", lambda _p: None)
    out = tmp_path / "results" / "ragas.json"

    assert ragas_run.main(["--limit", "3", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "faithfulness" in printed and "mean=0.900" in printed
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["summary"]["faithfulness"]["mean"] == pytest.approx(0.9)
    assert saved["scored"][0]["question"]
    assert "scored" in saved and "skipped" in saved


def test_main_stops_before_spending_when_ragas_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing() -> None:
        raise RagasNotInstalled("no module")

    monkeypatch.setattr(ragas_run, "check_ragas", missing)
    assert ragas_run.main([]) == 4
    assert ".venv-eval" in capsys.readouterr().err


def test_main_without_an_api_key_explains_itself(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_key(_settings: object) -> None:
        raise EmbeddingError("embedding_not_configured")

    # Stubbed so the result does not depend on whether the developer's real .env holds a key.
    monkeypatch.setattr(ragas_run, "check_ragas", lambda: None)
    monkeypatch.setattr(ragas_run, "get_embedder", no_key)
    assert ragas_run.main([]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_system_prompt_marker_used_by_the_recorder_exists() -> None:
    assert build_messages("q", [_hit("t")])[0]["content"] == SYSTEM_PROMPT
