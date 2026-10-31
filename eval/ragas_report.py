"""Turn per-question RAGAS scores into a summary. Pure Python, no ragas import."""

import math
from dataclasses import dataclass

from eval.ragas_samples import RagasSample, SkippedCase

METRICS = ("faithfulness", "answer_relevancy", "context_precision")

# What each score means, in plain words (shown in the report and in docs/ragas-evaluation.md).
METRIC_MEANING = {
    "faithfulness": "is every statement in the answer supported by the excerpts?",
    "answer_relevancy": "does the answer actually address the question?",
    "context_precision": "are the useful excerpts ranked first among the ones sent?",
}

# Below these a question is listed for manual review. They are review triggers, not pass marks.
REVIEW_BELOW = {"faithfulness": 0.80, "answer_relevancy": 0.70, "context_precision": 0.60}


@dataclass(frozen=True)
class ScoredSample:
    sample: RagasSample
    scores: dict[str, float | None]  # None = the judge failed on this one


def clean_score(value: object) -> float | None:
    """RAGAS reports NaN (or nothing) when the judge model failed; treat that as 'no score'."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return None if math.isnan(value) else float(value)


def summarize_scores(scored: list[ScoredSample]) -> dict[str, dict[str, float]]:
    """Mean, lowest and number of usable scores per metric."""
    summary: dict[str, dict[str, float]] = {}
    for metric in METRICS:
        values = [s.scores[metric] for s in scored if s.scores.get(metric) is not None]
        numbers = [v for v in values if v is not None]
        summary[metric] = {
            "mean": sum(numbers) / len(numbers) if numbers else float("nan"),
            "min": min(numbers) if numbers else float("nan"),
            "n": float(len(numbers)),
            "judge_failures": float(len(scored) - len(numbers)),
        }
    return summary


def needs_review(scored: list[ScoredSample]) -> dict[str, list[str]]:
    """Case ids to look at by hand, per metric: a low score, or the judge could not score it."""
    flagged: dict[str, list[str]] = {}
    for metric, limit in REVIEW_BELOW.items():
        flagged[metric] = [
            s.sample.case_id
            for s in scored
            if (s.scores.get(metric) is None or (s.scores[metric] or 0.0) < limit)
        ]
    return flagged


def format_report(
    scored: list[ScoredSample], skipped: list[SkippedCase], *, judge: str, embeddings: str
) -> str:
    lines = [
        f"RAGAS evaluation   judge={judge}   embeddings={embeddings}",
        f"  scored {len(scored)} answered questions; {len(skipped)} refused (not scored)",
    ]
    for metric, stats in summarize_scores(scored).items():
        failed = int(stats["judge_failures"])
        extra = f"   judge failures={failed}" if failed else ""
        lines.append(
            f"  {metric:<18} mean={stats['mean']:.3f}  min={stats['min']:.3f}  "
            f"n={int(stats['n'])}{extra}   ({METRIC_MEANING[metric]})"
        )
    flagged = needs_review(scored)
    lines.append("  review by hand (score below the review line or unscored):")
    for metric, ids in flagged.items():
        lines.append(f"    {metric:<18} <{REVIEW_BELOW[metric]:.2f}: {', '.join(ids) or '-'}")
    return "\n".join(lines)
