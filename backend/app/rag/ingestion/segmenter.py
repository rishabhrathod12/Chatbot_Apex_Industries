import re

from app.rag.ingestion.extractors.base import RawLine, RawPage
from app.schemas.extraction import Block, BlockKind, Page

_NUMBERED_HEADING = re.compile(r"^\d+(?:\.\d+)*\.\s+\S")
_KNOWN_HEADINGS = {"purpose", "document control", "important notice", "revision history"}
_BOILERPLATE_HEADINGS = {"important notice", "document control"}
_REVISION_HEADINGS = {"revision history"}


def is_heading(line: RawLine) -> bool:
    if line.heading:
        return True
    text = line.text
    if text.startswith("- "):
        return False
    if text.lower() in _KNOWN_HEADINGS:
        return True
    return (
        bool(_NUMBERED_HEADING.match(text)) and len(text) <= 100 and not text.endswith((".", ":"))
    )


def _kind_for_heading(heading: str) -> BlockKind:
    key = heading.lower()
    if key in _BOILERPLATE_HEADINGS:
        return "boilerplate"
    if key in _REVISION_HEADINGS:
        return "revision_history"
    return "body"


def _reflow(lines: list[str]) -> str:
    """Join PDF lines that were only wrapped visually. Each bullet starts a new paragraph."""
    units: list[str] = []
    for line in lines:
        if not units or line.startswith("- "):
            units.append(line)
        else:
            units[-1] = f"{units[-1]} {line}"
    return "\n".join(units)


def _make_block(kind: BlockKind, section: str | None, lines: list[str], reflow: bool) -> Block:
    keep_layout = kind in ("title_block", "revision_history")
    text = _reflow(lines) if reflow and not keep_layout else "\n".join(lines)
    return Block(kind=kind, section_title=section, text=text)


def build_pages(raw_pages: list[RawPage], *, title_block: bool, reflow: bool) -> list[Page]:
    """Group lines into typed blocks. Section state carries across page breaks.

    Each page keeps its own blocks, so a section spanning two pages still reports the right page
    for each piece of its text.
    """
    kind: BlockKind = "title_block" if title_block else "body"
    section: str | None = None
    pages: list[Page] = []

    for raw in raw_pages:
        blocks: list[Block] = []
        buffer: list[str] = []
        for line in raw.lines:
            if is_heading(line):
                if buffer:
                    blocks.append(_make_block(kind, section, buffer, reflow))
                    buffer = []
                section = line.text
                kind = _kind_for_heading(line.text)
            else:
                buffer.append(line.text)
        if buffer:
            blocks.append(_make_block(kind, section, buffer, reflow))
        pages.append(Page(number=raw.number, blocks=blocks))
    return pages
