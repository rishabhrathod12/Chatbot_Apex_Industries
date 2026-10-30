from app.rag.ingestion.cleaners import normalize_line, normalize_pages, strip_headers_footers
from app.rag.ingestion.extractors.base import RawLine, RawPage


def _page(number: int, *lines: str) -> RawPage:
    return RawPage(number, [RawLine(t) for t in lines])


def _texts(page: RawPage) -> list[str]:
    return [ln.text for ln in page.lines]


def test_normalize_line_unifies_bullets_and_spaces() -> None:
    assert normalize_line("•   Check   the\tguard") == "- Check the guard"
    assert normalize_line("▪ item") == "- item"


def test_normalize_line_removes_invisible_characters() -> None:
    assert normalize_line("lock­out") == "lockout"
    assert normalize_line("a b") == "a b"
    assert normalize_line("a\x00b\x07c") == "abc"


def test_normalize_line_keeps_dashes_and_unicode() -> None:
    assert normalize_line("Internal – Fictional") == "Internal – Fictional"


def test_normalize_pages_drops_empty_lines_and_keeps_numbers() -> None:
    result = normalize_pages([_page(3, "  ", "text", ""), _page(4)])
    assert [p.number for p in result] == [3, 4]
    assert _texts(result[0]) == ["text"]


def test_repeated_header_and_numbered_footer_are_removed() -> None:
    def page(n: int, word: str) -> RawPage:
        return _page(
            n,
            "Acme | CONFIDENTIAL",
            f"Page {n}",
            f"{word} intro",
            f"{word} detail one",
            "Shared middle line",
            f"{word} detail two",
            f"{word} outro",
            f"Acme footer {n}",
        )

    cleaned = strip_headers_footers([page(1, "First"), page(2, "Second"), page(3, "Third")])
    for p in cleaned:
        joined = " ".join(_texts(p))
        assert "CONFIDENTIAL" not in joined
        assert "Page " not in joined
        assert "footer" not in joined
    assert _texts(cleaned[1]) == [
        "Second intro",
        "Second detail one",
        "Shared middle line",
        "Second detail two",
        "Second outro",
    ]


def test_page_numbers_of_pages_are_preserved_after_cleaning() -> None:
    pages = [_page(1, "H", "a"), _page(2, "H", "b")]
    assert [p.number for p in strip_headers_footers(pages)] == [1, 2]


def test_repeated_line_in_the_middle_of_a_page_is_kept() -> None:
    body = ["l1", "l2", "l3", "Warning: hot surface", "l5", "l6", "l7"]
    pages = [_page(1, *body), _page(2, *body), _page(3, *body)]
    cleaned = strip_headers_footers(pages)
    assert "Warning: hot surface" in _texts(cleaned[0])


def test_line_repeated_on_too_few_pages_is_kept() -> None:
    pages = [
        _page(1, "Only here", "a", "b", "c", "d"),
        _page(2, "x", "a2", "b2", "c2", "d2"),
        _page(3, "y", "a3", "b3", "c3", "d3"),
    ]
    assert "Only here" in _texts(strip_headers_footers(pages)[0])


def test_single_page_is_untouched() -> None:
    page = _page(1, "Header", "Page 1", "Body")
    assert _texts(strip_headers_footers([page])[0]) == ["Header", "Page 1", "Body"]
