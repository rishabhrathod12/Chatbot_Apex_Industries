from pathlib import Path

import pymupdf

from app.rag.ingestion.extractors.base import ExtractionError, RawLine, RawPage

MAX_CHARS_PER_PAGE = 200_000


def extract_pdf(path: Path, max_pages: int) -> list[RawPage]:
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:
        raise ExtractionError("corrupt_pdf") from exc

    with doc:
        if doc.needs_pass:
            raise ExtractionError("encrypted_pdf")
        if doc.page_count > max_pages:
            raise ExtractionError("too_many_pages")

        pages: list[RawPage] = []
        for index in range(doc.page_count):
            try:
                text = doc[index].get_text("text")
            except Exception as exc:
                raise ExtractionError("corrupt_pdf") from exc
            if len(text) > MAX_CHARS_PER_PAGE:
                raise ExtractionError("page_too_large")
            pages.append(RawPage(index + 1, [RawLine(line) for line in text.splitlines()]))
    return pages
