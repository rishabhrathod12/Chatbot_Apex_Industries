import pytest

from app.rag.ingestion.chunkers import SectionChunker
from app.rag.ingestion.extractors import extract
from app.schemas.chunk import Chunk, DocumentContext
from app.schemas.extraction import ExtractedDocument
from tests.conftest import SAMPLE_DOCS
from tests.integration.test_extraction import SAMPLES

IDS = [s[0] for s in SAMPLES]


def _run(name: str, dept: str) -> tuple[ExtractedDocument, list[Chunk]]:
    document = extract(SAMPLE_DOCS / name, ".pdf")
    ctx = DocumentContext(
        document_id="doc1", scope="enterprise", owner_id="u1", source_name=name, department=dept
    )
    return document, SectionChunker().chunk(document, ctx)


@pytest.mark.parametrize(("name", "code", "dept", "title", "sentence"), SAMPLES, ids=IDS)
def test_every_chunk_traces_back_to_its_pages(
    name: str, code: str, dept: str, title: str, sentence: str
) -> None:
    document, chunks = _run(name, dept)
    page_text = {p.number: p.text for p in document.pages}
    assert chunks
    for chunk in chunks:
        assert chunk.page_start is not None
        assert chunk.page_end is not None
        allowed = " ".join(page_text[n] for n in range(chunk.page_start, chunk.page_end + 1))
        for line in chunk.text.split("\n"):
            assert line in allowed


@pytest.mark.parametrize(("name", "code", "dept", "title", "sentence"), SAMPLES, ids=IDS)
def test_known_sentence_is_cited_to_page_two(
    name: str, code: str, dept: str, title: str, sentence: str
) -> None:
    _, chunks = _run(name, dept)
    hits = [c for c in chunks if sentence in c.text]
    assert len(hits) == 1
    hit = hits[0]
    assert (hit.page_start, hit.page_end) == (2, 2)
    assert hit.section_title is not None
    assert hit.section_title[0].isdigit()
    assert hit.doc_code == code
    assert hit.doc_title == title
    assert hit.department == dept


@pytest.mark.parametrize(("name", "code", "dept", "title", "sentence"), SAMPLES, ids=IDS)
def test_boilerplate_and_revision_table_are_not_chunked(
    name: str, code: str, dept: str, title: str, sentence: str
) -> None:
    _, chunks = _run(name, dept)
    assert {c.kind for c in chunks} <= {"title_block", "body"}
    joined = "\n".join(c.text for c in chunks)
    assert "fictional sample content" not in joined
    assert "Prepared by" not in joined
    assert "Initial fictional sample for RAG testing" not in joined


@pytest.mark.parametrize(("name", "code", "dept", "title", "sentence"), SAMPLES, ids=IDS)
def test_document_id_is_searchable_through_the_title_chunk(
    name: str, code: str, dept: str, title: str, sentence: str
) -> None:
    _, chunks = _run(name, dept)
    first = chunks[0]
    assert first.kind == "title_block"
    assert code in first.text
    assert (first.page_start, first.page_end) == (1, 1)


@pytest.mark.parametrize(("name", "code", "dept", "title", "sentence"), SAMPLES, ids=IDS)
def test_chunk_ids_are_unique_sequential_and_deterministic(
    name: str, code: str, dept: str, title: str, sentence: str
) -> None:
    _, first = _run(name, dept)
    _, second = _run(name, dept)
    assert [c.chunk_index for c in first] == list(range(len(first)))
    assert len({c.chunk_id for c in first}) == len(first)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert [c.content_hash for c in first] == [c.content_hash for c in second]
    assert first[0].chunk_id == "doc1:1.0:0"


@pytest.mark.parametrize(("name", "code", "dept", "title", "sentence"), SAMPLES, ids=IDS)
def test_one_chunk_per_section_for_these_short_documents(
    name: str, code: str, dept: str, title: str, sentence: str
) -> None:
    document, chunks = _run(name, dept)
    cover = 1  # title block
    purpose = 1
    numbered = len(document.pages[1].blocks)
    assert len(chunks) == cover + purpose + numbered
