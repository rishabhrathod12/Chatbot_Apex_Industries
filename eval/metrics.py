"""Retrieval metrics. These need no LLM, so they are free to run and fully deterministic."""

import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

GOLDEN_SET = Path(__file__).resolve().parent / "golden_set.json"
ANSWERABLE_TYPES = {"answerable", "multi_document", "exact_id"}


@dataclass(frozen=True)
class Expected:
    doc_code: str
    page: int | None


@dataclass(frozen=True)
class Case:
    id: str
    type: str
    question: str
    expected: tuple[Expected, ...]
    tag: str = ""  # keyword | paraphrase | near_miss, or empty for the original cases

    @property
    def answerable(self) -> bool:
        return self.type in ANSWERABLE_TYPES

    @property
    def not_specified(self) -> bool:
        """The documents say outright that they don't give this value."""
        return self.type == "not_specified"

    @property
    def refusal_expected(self) -> bool:
        """Nothing relevant exists, so the only right outcome is a refusal."""
        return self.type == "unanswerable"


@dataclass(frozen=True)
class Followup:
    """A two-turn conversation: the second question only makes sense after the first."""

    id: str
    first_question: str
    question: str
    expected: tuple[Expected, ...]


@dataclass(frozen=True)
class RetrievedChunk:
    """What the metrics need to know about one retrieved chunk."""

    doc_code: str
    page_start: int | None
    page_end: int | None
    score: float


def load_golden(path: Path = GOLDEN_SET) -> list[Case]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        Case(
            id=c["id"],
            type=c["type"],
            question=c["question"],
            expected=tuple(Expected(e["doc_code"], e.get("page")) for e in c["expected"]),
            tag=c.get("tag", ""),
        )
        for c in raw["cases"]
    ]


def load_followups(path: Path = GOLDEN_SET) -> list[Followup]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        Followup(
            id=f["id"],
            first_question=f["first_question"],
            question=f["question"],
            expected=tuple(Expected(e["doc_code"], e.get("page")) for e in f["expected"]),
        )
        for f in raw.get("followups", [])
    ]


def _matches(chunk: RetrievedChunk, expected: Expected, *, check_page: bool) -> bool:
    if chunk.doc_code != expected.doc_code:
        return False
    if not check_page or expected.page is None or chunk.page_start is None:
        return True
    end = chunk.page_end if chunk.page_end is not None else chunk.page_start
    return chunk.page_start <= expected.page <= end


def first_hit_rank(
    chunks: Sequence[RetrievedChunk], expected: Sequence[Expected], *, check_page: bool
) -> int | None:
    """1-based rank of the first retrieved chunk that matches any expected source."""
    for rank, chunk in enumerate(chunks, start=1):
        if any(_matches(chunk, e, check_page=check_page) for e in expected):
            return rank
    return None


def summarize(ranks: Sequence[int | None], ks: Sequence[int] = (1, 3, 5)) -> dict[str, float]:
    """Hit rate at each k, and mean reciprocal rank (a miss counts as 0)."""
    n = len(ranks)
    if n == 0:
        return {}
    result = {f"hit@{k}": sum(1 for r in ranks if r is not None and r <= k) / n for k in ks}
    result["mrr"] = sum(1 / r for r in ranks if r is not None) / n
    result["n"] = float(n)
    return result


def score_spread(
    answerable_top: Sequence[float], unanswerable_top: Sequence[float]
) -> dict[str, float]:
    """Top-1 similarity of answerable vs unanswerable questions. If the two ranges overlap, no
    single relevance threshold can separate them. That is what we need to know to set one."""
    out: dict[str, float] = {}
    if answerable_top:
        out["answerable_min"] = min(answerable_top)
        out["answerable_median"] = statistics.median(answerable_top)
    if unanswerable_top:
        out["unanswerable_max"] = max(unanswerable_top)
        out["unanswerable_median"] = statistics.median(unanswerable_top)
    return out
