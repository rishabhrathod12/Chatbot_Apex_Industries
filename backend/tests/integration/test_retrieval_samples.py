"""Retrieval over the six real sample PDFs, using the offline hashing embedder.

The hashing embedder matches on shared words, not meaning. These tests therefore check the
plumbing (right document, right page, filters respected) and act as a regression baseline. They do
NOT measure semantic retrieval quality. That needs the real embedder: `python -m eval.run`.
"""

import pytest

from app.rag.retrieval import semantic_search
from app.rag.vectorstore import SearchFilter
from tests.conftest import IndexedSamples

# (question, expected document code). Same questions as eval/golden_set.json, section A.
QUESTIONS = [
    ("What must be checked before operating a CNC machine?", "APM-PROD-MAN-001"),
    ("Who may operate CNC equipment?", "APM-PROD-MAN-001"),
    ("What should an operator do if a guard or interlock seems faulty?", "APM-PROD-MAN-001"),
    ("What information should I give when reporting a machine fault?", "APM-MNT-MAN-002"),
    ("What should I do if a machine shows an over-temperature indication?", "APM-MNT-MAN-002"),
    ("Can I keep resetting an alarm to keep production moving?", "APM-MNT-MAN-002"),
    ("What are the steps of lockout tagout energy isolation?", "APM-HSE-MAN-003"),
    ("Can an emergency stop be used as the energy isolation method?", "APM-HSE-MAN-003"),
    ("What should I do before handling a chemical?", "APM-HSE-MAN-003"),
    ("When can I re-enter an evacuated area?", "APM-HSE-MAN-003"),
    ("What must happen before batch production continues after first piece?", "APM-PROD-SOP-004"),
    ("What should I record during in-process checks?", "APM-PROD-SOP-004"),
    ("How do I submit a leave request?", "APM-HR-POL-005"),
    ("What should I do about a missed attendance punch?", "APM-HR-POL-005"),
    ("Who can release a product after final inspection?", "APM-QLT-SOP-006"),
    ("What goes into a nonconformance report?", "APM-QLT-SOP-006"),
]

# Known misses of the word-matching embedder (the correct chunks exist and carry the right metadata,
# the embedder just can't connect these questions to them). A real embedder is measured with
# `python -m eval.run --provider openai`. strict=True: if one starts passing, the test fails so the
# baseline gets updated.
_LEXICAL_MISS = pytest.mark.xfail(strict=True, reason="needs a semantic embedder, not word overlap")
_KNOWN_MISSES = {
    "What should an operator do if a guard or interlock seems faulty?",
    "What information should I give when reporting a machine fault?",
}
_CASES = [
    pytest.param(q, c, marks=_LEXICAL_MISS) if q in _KNOWN_MISSES else pytest.param(q, c)
    for q, c in QUESTIONS
]


@pytest.mark.parametrize(("question", "code"), _CASES)
def test_expected_document_is_in_the_top_three(
    indexed_samples: IndexedSamples, question: str, code: str
) -> None:
    hits = semantic_search(indexed_samples.embedder, indexed_samples.store, question, top_k=3)
    assert code in [h.metadata["doc_code"] for h in hits], [h.metadata["doc_code"] for h in hits]


def test_emergency_stop_question_finds_safety_or_production(
    indexed_samples: IndexedSamples,
) -> None:
    hits = semantic_search(
        indexed_samples.embedder, indexed_samples.store, "emergency stop devices", top_k=1
    )
    assert hits[0].metadata["department"] in {"safety", "production"}
    assert "emergency" in hits[0].text.lower()


def test_hit_carries_everything_a_citation_needs(indexed_samples: IndexedSamples) -> None:
    hit = semantic_search(
        indexed_samples.embedder,
        indexed_samples.store,
        "How do I submit a leave request?",
        top_k=1,
    )[0]
    assert hit.metadata["doc_code"] == "APM-HR-POL-005"
    assert hit.metadata["doc_title"] == "Employee Attendance & Leave Policy"
    assert hit.metadata["section_title"] == "3. Leave request process"
    assert (hit.metadata["page_start"], hit.metadata["page_end"]) == (2, 2)
    assert str(hit.metadata["source_name"]).endswith(".pdf")


def test_permission_filter_hides_other_departments(indexed_samples: IndexedSamples) -> None:
    question = "How do I submit a leave request?"
    embedder, store = indexed_samples.embedder, indexed_samples.store
    unrestricted = semantic_search(embedder, store, question, top_k=3)
    assert unrestricted[0].metadata["department"] == "hr"

    safety_only = semantic_search(
        embedder, store, question, top_k=10, search_filter=SearchFilter(departments=["safety"])
    )
    assert safety_only
    assert {h.metadata["department"] for h in safety_only} == {"safety"}
    assert all(h.metadata["doc_code"] != "APM-HR-POL-005" for h in safety_only)


def test_department_without_documents_returns_nothing(indexed_samples: IndexedSamples) -> None:
    hits = semantic_search(
        indexed_samples.embedder,
        indexed_samples.store,
        "goods receipt procedure",
        search_filter=SearchFilter(departments=["stores"]),
    )
    assert hits == []
