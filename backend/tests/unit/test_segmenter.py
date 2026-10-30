import pytest

from app.rag.ingestion.extractors.base import RawLine, RawPage
from app.rag.ingestion.segmenter import build_pages, is_heading


def _page(number: int | None, *lines: str) -> RawPage:
    return RawPage(number, [RawLine(t) for t in lines])


@pytest.mark.parametrize(
    "text",
    [
        "1. Scope and responsibilities",
        "12. Records",
        "2.1. Sub section",
        "Purpose",
        "REVISION HISTORY",
    ],
)
def test_headings_detected(text: str) -> None:
    assert is_heading(RawLine(text))


@pytest.mark.parametrize(
    "text",
    ["1.0 – Sample", "1. Do this first.", "- 1. item", "Some ordinary sentence", "1.0", "Page 2"],
)
def test_non_headings(text: str) -> None:
    assert not is_heading(RawLine(text))


def test_format_flagged_heading_is_a_heading() -> None:
    assert is_heading(RawLine("Anything", heading=True))


def test_blocks_carry_section_titles() -> None:
    pages = build_pages(
        [_page(1, "1. Scope", "- a", "2. Records", "- b")], title_block=False, reflow=True
    )
    blocks = pages[0].blocks
    assert [(b.section_title, b.text) for b in blocks] == [
        ("1. Scope", "- a"),
        ("2. Records", "- b"),
    ]
    assert all(b.kind == "body" for b in blocks)


def test_reflow_joins_wrapped_lines_but_keeps_bullets_apart() -> None:
    raw = [_page(1, "1. Scope", "- first part", "second part", "- another bullet")]
    block = build_pages(raw, title_block=False, reflow=True)[0].blocks[0]
    assert block.text == "- first part second part\n- another bullet"


def test_no_reflow_keeps_lines_for_docx_and_txt() -> None:
    raw = [_page(None, "Heading line 1.", "one", "two")]
    block = build_pages(raw, title_block=False, reflow=False)[0].blocks[0]
    assert block.text == "Heading line 1.\none\ntwo"


def test_section_state_carries_across_pages() -> None:
    raw = [
        _page(1, "1. Scope", "- text on page one", "Revision history"),
        _page(2, "Version", "Date", "1.0"),
    ]
    pages = build_pages(raw, title_block=False, reflow=True)
    assert [b.kind for b in pages[0].blocks] == ["body"]  # empty "Revision history" block skipped
    assert pages[1].blocks[0].kind == "revision_history"
    assert pages[1].blocks[0].section_title == "Revision history"
    assert pages[1].blocks[0].text == "Version\nDate\n1.0"  # layout kept for tables


def test_section_spanning_pages_keeps_each_pages_number() -> None:
    raw = [_page(1, "2. Steps", "- one"), _page(2, "- two")]
    pages = build_pages(raw, title_block=False, reflow=True)
    assert [(p.number, p.blocks[0].section_title) for p in pages] == [
        (1, "2. Steps"),
        (2, "2. Steps"),
    ]


def test_boilerplate_headings_are_tagged() -> None:
    raw = [_page(1, "IMPORTANT NOTICE", "disclaimer text", "Purpose", "real content")]
    blocks = build_pages(raw, title_block=False, reflow=True)[0].blocks
    assert [(b.kind, b.section_title) for b in blocks] == [
        ("boilerplate", "IMPORTANT NOTICE"),
        ("body", "Purpose"),
    ]


def test_title_block_only_before_first_heading() -> None:
    raw = [_page(1, "Company", "Title", "Document ID", "X-1", "Purpose", "text")]
    blocks = build_pages(raw, title_block=True, reflow=True)[0].blocks
    assert [b.kind for b in blocks] == ["title_block", "body"]
    assert blocks[0].text == "Company\nTitle\nDocument ID\nX-1"
