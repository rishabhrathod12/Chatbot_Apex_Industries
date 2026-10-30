import hashlib
import re
from dataclasses import dataclass

from app.rag.ingestion.chunkers.base import CHARS_PER_TOKEN
from app.schemas.chunk import Chunk, DocumentContext
from app.schemas.extraction import Block, BlockKind, ExtractedDocument

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_DEFAULT_KINDS: frozenset[BlockKind] = frozenset({"body", "title_block"})


@dataclass(frozen=True)
class _Unit:
    """The smallest piece we never split on purpose (a bullet or a paragraph), with its page."""

    text: str
    page: int | None


@dataclass
class _Section:
    title: str | None
    kind: BlockKind
    units: list[_Unit]


def _split_oversized(text: str, max_chars: int) -> list[str]:
    """Split one unit that is bigger than a whole chunk: by sentence, then by hard cut."""
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            pieces.append(current)
        current = sentence
        while len(current) > max_chars:
            pieces.append(current[:max_chars])
            current = current[max_chars:]
    if current:
        pieces.append(current)
    return pieces


def _units_for(block: Block, page: int | None, max_chars: int) -> list[_Unit]:
    units: list[_Unit] = []
    for line in block.text.split("\n"):
        if not line.strip():
            continue
        if len(line) <= max_chars:
            units.append(_Unit(line, page))
        else:
            units.extend(_Unit(piece, page) for piece in _split_oversized(line, max_chars))
    return units


def _size(units: list[_Unit]) -> int:
    return sum(len(u.text) for u in units) + max(0, len(units) - 1)  # +1 per newline


def _overlap_tail(units: list[_Unit], overlap_chars: int) -> list[_Unit]:
    """Trailing units of a finished chunk to repeat at the start of the next one.

    The first unit is never included, so the next chunk always moves forward.
    """
    tail: list[_Unit] = []
    for unit in reversed(units[1:]):
        if _size([unit, *tail]) > overlap_chars:
            break
        tail.insert(0, unit)
    return tail


def _pack(units: list[_Unit], max_chars: int, overlap_chars: int) -> list[list[_Unit]]:
    groups: list[list[_Unit]] = []
    current: list[_Unit] = []
    for unit in units:
        if current and _size([*current, unit]) > max_chars:
            groups.append(current)
            current = _overlap_tail(current, overlap_chars)
            if current and _size([*current, unit]) > max_chars:
                current = []  # the overlap doesn't fit next to this unit, so skip it
        current.append(unit)
    if current:
        groups.append(current)
    return groups


class SectionChunker:
    """Splits each section on its own, packing bullets/paragraphs up to a size limit.

    Every unit remembers its page, so a chunk's page range is exact even when a section runs over
    a page break. Only `include_kinds` are indexed (cover block and body by default).
    """

    def __init__(
        self,
        max_tokens: int = 600,
        overlap_tokens: int = 80,
        include_kinds: frozenset[BlockKind] = _DEFAULT_KINDS,
    ) -> None:
        if max_tokens <= 0 or not 0 <= overlap_tokens < max_tokens // 2:
            raise ValueError("overlap_tokens must be >= 0 and below half of max_tokens")
        self.max_chars = max_tokens * CHARS_PER_TOKEN
        self.overlap_chars = overlap_tokens * CHARS_PER_TOKEN
        self.include_kinds = include_kinds

    def _sections(self, document: ExtractedDocument) -> list[_Section]:
        sections: list[_Section] = []
        for page in document.pages:
            for block in page.blocks:
                if block.kind not in self.include_kinds:
                    continue
                units = _units_for(block, page.number, self.max_chars)
                last = sections[-1] if sections else None
                if last and last.title == block.section_title and last.kind == block.kind:
                    last.units.extend(units)
                else:
                    sections.append(_Section(block.section_title, block.kind, units))
        return sections

    def chunk(self, document: ExtractedDocument, context: DocumentContext) -> list[Chunk]:
        meta = document.metadata
        version = context.doc_version or meta.version or "1"
        chunks: list[Chunk] = []
        for section in self._sections(document):
            for group in _pack(section.units, self.max_chars, self.overlap_chars):
                text = "\n".join(u.text for u in group)
                pages = [u.page for u in group if u.page is not None]
                index = len(chunks)
                chunks.append(
                    Chunk(
                        chunk_id=f"{context.document_id}:{version}:{index}",
                        document_id=context.document_id,
                        chunk_index=index,
                        text=text,
                        kind=section.kind,
                        section_title=section.title,
                        page_start=min(pages) if pages else None,
                        page_end=max(pages) if pages else None,
                        doc_code=meta.doc_code,
                        doc_title=meta.title,
                        doc_version=version,
                        scope=context.scope,
                        department=context.department,
                        visibility=context.visibility,
                        owner_id=context.owner_id,
                        source_name=context.source_name,
                        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    )
                )
        return chunks
