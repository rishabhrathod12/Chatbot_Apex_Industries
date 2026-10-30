"""M8 acceptance: every source quote must be text that really is on the cited page."""

import pytest

from app.rag.generation import answer_question
from app.rag.ingestion.extractors import extract
from eval.metrics import load_golden
from tests.conftest import SAMPLE_DOCS, IndexedSamples, ScriptedChat


@pytest.fixture(scope="module")
def page_text() -> dict[str, dict[int, str]]:
    """document code -> page number -> cleaned text of that page."""
    pages: dict[str, dict[int, str]] = {}
    for pdf in SAMPLE_DOCS.glob("*.pdf"):
        document = extract(pdf, ".pdf")
        assert document.metadata.doc_code
        pages[document.metadata.doc_code] = {p.number or 0: p.text for p in document.pages}
    return pages


@pytest.mark.parametrize("case", [c for c in load_golden() if c.answerable], ids=lambda c: c.id)
def test_every_quote_is_on_a_cited_page(
    case, indexed_samples: IndexedSamples, page_text: dict[str, dict[int, str]]
) -> None:  # noqa: ANN001
    reply = ScriptedChat("It is covered [1]. Also see this [2]. And this [3].")
    result = answer_question(
        case.question,
        embedder=indexed_samples.embedder,
        store=indexed_samples.store,
        chat_model=reply,
        top_k=3,
        min_score=0.0,
    )
    assert result.status == "answered"
    assert result.sources
    for source in result.sources:
        assert source.quote, source
        assert source.page_start is not None
        end = source.page_end or source.page_start
        cited_pages = " ".join(
            page_text[source.doc_code][n] for n in range(source.page_start, end + 1)
        )
        assert source.quote in cited_pages, (source.doc_code, source.quote)
