import math
import re
from collections import Counter

from app.rag.ingestion.extractors.base import RawLine, RawPage

_BULLETS = "•▪◦●‣·"
_SPACES = re.compile(r"[ \t]+")
_DIGITS = re.compile(r"\d+")
_EDGE_LINES = 3  # header/footer lines live in the first/last few lines of a page


def normalize_line(text: str) -> str:
    """Tidy one line: strip control chars, soft hyphens and odd spaces, and unify bullets."""
    text = text.replace("­", "").replace(" ", " ")
    text = "".join(ch for ch in text if ch.isprintable() or ch == "\t")
    text = _SPACES.sub(" ", text).strip()
    if text and text[0] in _BULLETS:
        text = "- " + text[1:].lstrip()
    return text


def normalize_pages(pages: list[RawPage]) -> list[RawPage]:
    """Normalise every line and drop empty ones, keeping the heading flags and page numbers."""
    result: list[RawPage] = []
    for page in pages:
        lines = [RawLine(normalize_line(ln.text), ln.heading) for ln in page.lines]
        result.append(RawPage(page.number, [ln for ln in lines if ln.text]))
    return result


def _edge_indices(page: RawPage) -> set[int]:
    n = len(page.lines)
    return set(range(min(_EDGE_LINES, n))) | set(range(max(0, n - _EDGE_LINES), n))


def _key(text: str) -> str:
    # "Page 2" and "Page 3" must count as the same repeated line.
    return _DIGITS.sub("#", text.lower())


def strip_headers_footers(pages: list[RawPage]) -> list[RawPage]:
    """Remove lines that repeat near the top/bottom of most pages (running headers and footers).

    Only the first and last few lines of each page are considered, so a body line that merely
    happens to repeat is never removed. A single page can't show repetition, so it is untouched.
    """
    if len(pages) < 2:
        return pages

    threshold = max(2, math.ceil(0.6 * len(pages)))
    counts: Counter[str] = Counter()
    for page in pages:
        counts.update({_key(page.lines[i].text) for i in _edge_indices(page)})
    repeated = {key for key, count in counts.items() if count >= threshold}

    result: list[RawPage] = []
    for page in pages:
        drop = {i for i in _edge_indices(page) if _key(page.lines[i].text) in repeated}
        result.append(
            RawPage(page.number, [ln for i, ln in enumerate(page.lines) if i not in drop])
        )
    return result
