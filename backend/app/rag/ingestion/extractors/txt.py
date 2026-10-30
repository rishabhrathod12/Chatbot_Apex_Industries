from pathlib import Path

from app.rag.ingestion.extractors.base import ExtractionError, RawLine, RawPage


def extract_txt(path: Path) -> list[RawPage]:
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExtractionError("invalid_encoding") from exc
    return [RawPage(None, [RawLine(line) for line in text.splitlines()])]
