"""Checks shared by every answering path.

Citation markers must point at excerpts that were really sent, and the sources shown to the user
are built from stored chunk data, never from model output."""

import re

from app.rag.generation.citations import select_quote
from app.rag.vectorstore import SearchHit
from app.schemas.chat import Source

_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def check_citations(text: str, available: int) -> tuple[str, list[int]]:
    """Keep only markers that point at an excerpt we really sent. Returns cleaned text and the
    excerpt numbers used, in order of first appearance."""
    used: list[int] = []

    def keep_valid(match: re.Match[str]) -> str:
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        valid = [n for n in numbers if 1 <= n <= available]
        for n in valid:
            if n not in used:
                used.append(n)
        return f"[{', '.join(str(n) for n in valid)}]" if valid else ""

    cleaned = _MARKER.sub(keep_valid, text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    return cleaned.strip(), used


def source_from_hit(number: int, hit: SearchHit, answer: str) -> Source:
    meta = hit.metadata
    start, end = meta.get("page_start"), meta.get("page_end")
    return Source(
        number=number,
        chunk_id=hit.chunk_id,
        doc_code=str(meta.get("doc_code", "")),
        doc_title=str(meta.get("doc_title", "")),
        source_name=str(meta.get("source_name", "")),
        section_title=str(meta.get("section_title", "")),
        page_start=start if isinstance(start, int) else None,
        page_end=end if isinstance(end, int) else None,
        quote=select_quote(answer, number, hit.text),
    )
