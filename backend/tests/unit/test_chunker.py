import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.rag.ingestion.chunkers import SectionChunker, estimate_tokens
from app.schemas.chunk import DocumentContext
from app.schemas.extraction import Block, BlockKind, DocumentMetadata, ExtractedDocument, Page

CTX = DocumentContext(
    document_id="d1",
    scope="enterprise",
    owner_id="u1",
    source_name="manual.pdf",
    department="safety",
)


def _block(text: str, section: str | None = "1. Rules", kind: BlockKind = "body") -> Block:
    return Block(kind=kind, section_title=section, text=text)


def _doc(*pages: Page, **meta: str) -> ExtractedDocument:
    return ExtractedDocument(pages=list(pages), metadata=DocumentMetadata(**meta))


def test_small_sections_become_one_chunk_each() -> None:
    doc = _doc(
        Page(number=1, blocks=[_block("- a\n- b", "1. One"), _block("- c", "2. Two")]),
        version="1.0",
        doc_code="X-1",
        title="Manual",
    )
    chunks = SectionChunker().chunk(doc, CTX)
    assert [(c.section_title, c.text) for c in chunks] == [
        ("1. One", "- a\n- b"),
        ("2. Two", "- c"),
    ]
    assert [c.chunk_id for c in chunks] == ["d1:1.0:0", "d1:1.0:1"]
    assert [c.chunk_index for c in chunks] == [0, 1]
    assert chunks[0].doc_code == "X-1"
    assert chunks[0].doc_title == "Manual"
    assert chunks[0].department == "safety"
    assert len(chunks[0].content_hash) == 64


def test_page_range_follows_section_across_a_page_break() -> None:
    doc = _doc(
        Page(number=1, blocks=[_block("- one")]),
        Page(number=2, blocks=[_block("- two")]),
        Page(number=3, blocks=[_block("- three", "2. Next")]),
    )
    chunks = SectionChunker().chunk(doc, CTX)
    assert [(c.section_title, c.page_start, c.page_end) for c in chunks] == [
        ("1. Rules", 1, 2),
        ("2. Next", 3, 3),
    ]


def _long_section() -> ExtractedDocument:
    # 12 bullets of 40 characters, 6 on page 1 and 6 on page 2.
    def bullets(start: int) -> str:
        return "\n".join(f"- bullet number {i:02d} " + "x" * 20 for i in range(start, start + 6))

    return _doc(
        Page(number=1, blocks=[_block(bullets(0))]), Page(number=2, blocks=[_block(bullets(6))])
    )


def test_large_section_is_split_within_the_size_limit() -> None:
    chunker = SectionChunker(max_tokens=50, overlap_tokens=0)
    chunks = chunker.chunk(_long_section(), CTX)
    assert len(chunks) > 1
    assert all(len(c.text) <= 50 * 4 for c in chunks)
    assert all(estimate_tokens(c.text) <= 50 for c in chunks)
    lines = [ln for c in chunks for ln in c.text.split("\n")]
    assert len(lines) == len(set(lines)) == 12  # nothing lost, nothing repeated


def test_split_chunks_report_exact_pages() -> None:
    chunks = SectionChunker(max_tokens=50, overlap_tokens=0).chunk(_long_section(), CTX)
    for chunk in chunks:
        numbers = [int(ln.split()[3]) for ln in chunk.text.split("\n")]
        expected_pages = {1 if n < 6 else 2 for n in numbers}
        assert chunk.page_start == min(expected_pages)
        assert chunk.page_end == max(expected_pages)


def test_overlap_repeats_trailing_units_in_the_next_chunk() -> None:
    chunks = SectionChunker(max_tokens=50, overlap_tokens=12).chunk(_long_section(), CTX)
    assert len(chunks) > 1
    for before, after in zip(chunks, chunks[1:], strict=False):
        assert before.text.split("\n")[-1] == after.text.split("\n")[0]
    covered = {ln for c in chunks for ln in c.text.split("\n")}
    assert len(covered) == 12


def test_oversized_line_is_split_by_sentence() -> None:
    sentence = "This is a fairly long sentence about safety rules that goes on. "
    doc = _doc(Page(number=1, blocks=[_block((sentence * 8).strip())]))
    chunks = SectionChunker(max_tokens=50, overlap_tokens=0).chunk(doc, CTX)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)
    assert all(c.text.endswith(".") for c in chunks)  # cut at sentence ends


def test_unbreakable_text_is_hard_cut() -> None:
    doc = _doc(Page(number=1, blocks=[_block("x" * 1000)]))
    chunks = SectionChunker(max_tokens=50, overlap_tokens=0).chunk(doc, CTX)
    assert all(len(c.text) <= 200 for c in chunks)
    assert "".join(c.text for c in chunks) == "x" * 1000


def test_boilerplate_and_revision_history_are_not_indexed() -> None:
    doc = _doc(
        Page(
            number=1,
            blocks=[
                _block("cover", None, "title_block"),
                _block("disclaimer", "IMPORTANT NOTICE", "boilerplate"),
                _block("real", "Purpose"),
            ],
        ),
        Page(number=2, blocks=[_block("v1.0 table", "Revision history", "revision_history")]),
    )
    chunks = SectionChunker().chunk(doc, CTX)
    assert [(c.kind, c.text) for c in chunks] == [("title_block", "cover"), ("body", "real")]


def test_include_kinds_can_be_changed() -> None:
    doc = _doc(Page(number=1, blocks=[_block("x", "Revision history", "revision_history")]))
    chunker = SectionChunker(include_kinds=frozenset({"revision_history"}))
    assert [c.kind for c in chunker.chunk(doc, CTX)] == ["revision_history"]


def test_chunking_is_deterministic() -> None:
    chunker = SectionChunker(max_tokens=50, overlap_tokens=12)
    first = [c.model_dump() for c in chunker.chunk(_long_section(), CTX)]
    second = [c.model_dump() for c in chunker.chunk(_long_section(), CTX)]
    assert first == second


def test_docx_chunks_have_no_pages_and_no_page_metadata() -> None:
    doc = _doc(Page(number=None, blocks=[_block("- text")]))
    chunk = SectionChunker().chunk(doc, CTX)[0]
    assert chunk.page_start is None
    assert chunk.page_end is None
    assert "page_start" not in chunk.to_metadata()


def test_empty_document_gives_no_chunks() -> None:
    assert SectionChunker().chunk(_doc(Page(number=1, blocks=[])), CTX) == []


def test_version_comes_from_context_then_metadata_then_default() -> None:
    doc = _doc(Page(number=1, blocks=[_block("x")]), version="3.0")
    assert SectionChunker().chunk(doc, CTX)[0].doc_version == "3.0"
    explicit = CTX.model_copy(update={"doc_version": "9"})
    assert SectionChunker().chunk(doc, explicit)[0].chunk_id == "d1:9:0"
    assert (
        SectionChunker().chunk(_doc(Page(number=1, blocks=[_block("x")])), CTX)[0].doc_version
        == "1"
    )


def test_metadata_is_flat_and_free_of_none() -> None:
    doc = _doc(Page(number=2, blocks=[_block("x", None)]))
    meta = SectionChunker().chunk(doc, CTX)[0].to_metadata()
    assert all(isinstance(v, str | int) for v in meta.values())
    assert meta["scope"] == "enterprise"
    assert meta["department"] == "safety"
    assert meta["owner_id"] == "u1"
    assert meta["page_start"] == meta["page_end"] == 2
    assert meta["section_title"] == ""


def test_embedding_text_adds_document_and_section_context() -> None:
    doc = _doc(Page(number=1, blocks=[_block("- wear gloves", "2. PPE")]), title="Safety Manual")
    assert (
        SectionChunker().chunk(doc, CTX)[0].embedding_text
        == "Safety Manual > 2. PPE\n- wear gloves"
    )
    plain = _doc(Page(number=1, blocks=[_block("- wear gloves", None)]))
    assert SectionChunker().chunk(plain, CTX)[0].embedding_text == "manual.pdf\n- wear gloves"


@pytest.mark.parametrize(("max_tokens", "overlap"), [(0, 0), (-5, 0), (100, 50), (100, -1)])
def test_invalid_chunker_sizes_rejected(max_tokens: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="overlap"):
        SectionChunker(max_tokens=max_tokens, overlap_tokens=overlap)


def test_estimate_tokens_rounds_up() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_settings_reject_bad_chunk_sizes() -> None:
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP_TOKENS"):
        Settings(_env_file=None, chunk_max_tokens=100, chunk_overlap_tokens=60)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="CHUNK_MAX_TOKENS"):
        Settings(_env_file=None, chunk_max_tokens=10, chunk_overlap_tokens=0)  # type: ignore[call-arg]
