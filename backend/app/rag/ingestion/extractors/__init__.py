from pathlib import Path

from app.rag.ingestion.cleaners import normalize_pages, strip_headers_footers
from app.rag.ingestion.extractors.base import ExtractionError, RawPage
from app.rag.ingestion.extractors.docx import extract_docx
from app.rag.ingestion.extractors.pdf import extract_pdf
from app.rag.ingestion.extractors.txt import extract_txt
from app.rag.ingestion.metadata import front_matter, parse_metadata
from app.rag.ingestion.segmenter import build_pages
from app.schemas.extraction import DocumentMetadata, ExtractedDocument

__all__ = ["ExtractionError", "extract"]

DEFAULT_MAX_PAGES = 200


def extract(path: Path, ext: str, *, max_pages: int = DEFAULT_MAX_PAGES) -> ExtractedDocument:
    """Turn a stored file into clean, page-aware text plus cover-page metadata."""
    raw: list[RawPage]
    if ext == ".pdf":
        raw = extract_pdf(path, max_pages)
    elif ext == ".docx":
        raw = extract_docx(path)
    elif ext == ".txt":
        raw = extract_txt(path)
    else:
        raise ExtractionError("unsupported_type")

    is_pdf = ext == ".pdf"
    pages = normalize_pages(raw)
    if is_pdf:
        pages = strip_headers_footers(pages)

    if not any(page.lines for page in pages):
        raise ExtractionError("no_text_layer" if is_pdf else "empty_document")

    metadata, warnings = parse_metadata(front_matter(pages[0].lines))
    has_title_block = metadata.doc_code is not None
    built = build_pages(pages, title_block=has_title_block, reflow=is_pdf)
    return ExtractedDocument(
        pages=built,
        metadata=metadata if has_title_block else DocumentMetadata(),
        warnings=warnings if has_title_block else [],
    )
