from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.rag.ingestion.extractors.base import ExtractionError, RawLine, RawPage


def _is_heading_style(paragraph: Paragraph) -> bool:
    style = paragraph.style.name if paragraph.style is not None else ""
    # "Title" is deliberately not a heading: it sits on the cover page, before the metadata table.
    return style.startswith("Heading")


def _table_lines(table: Table) -> list[RawLine]:
    lines: list[RawLine] = []
    for row in table.rows:
        cells: list[str] = []
        previous_tc = None
        for cell in row.cells:
            if cell._tc is previous_tc:  # merged cells repeat the same underlying cell
                continue
            previous_tc = cell._tc
            if cell.text.strip():
                cells.append(cell.text.strip())
        if cells:
            lines.append(RawLine(" | ".join(cells)))
    return lines


def extract_docx(path: Path) -> list[RawPage]:
    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise ExtractionError("corrupt_docx") from exc

    lines: list[RawLine] = []
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            lines.append(RawLine(item.text, heading=_is_heading_style(item)))
        else:
            lines.extend(_table_lines(item))
    # DOCX is a flow format with no stable pages, so everything is one unnumbered page.
    return [RawPage(None, lines)]
