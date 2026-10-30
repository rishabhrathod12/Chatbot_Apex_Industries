import re
from datetime import date, datetime

from app.core.departments import canonical_department
from app.rag.ingestion.extractors.base import RawLine
from app.rag.ingestion.segmenter import is_heading
from app.schemas.extraction import DocumentMetadata

_LABELS = {
    "document id": "doc_code",
    "department": "department",
    "version": "version",
    "effective date": "effective_date",
    "document owner": "owner",
    "classification": "classification",
}
_DATE_FORMATS = ("%d %B %Y", "%d-%b-%Y", "%Y-%m-%d")
_VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)", re.IGNORECASE)


def front_matter(lines: list[RawLine]) -> list[str]:
    """The lines at the top of the first page, up to the first section heading."""
    result: list[str] = []
    for line in lines:
        if is_heading(line):
            break
        result.append(line.text)
    return result


def _parse_date(value: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _find_fields(lines: list[str]) -> dict[str, str]:
    # A table comes out as label/value on separate lines (PDF) or "label | value" (DOCX).
    tokens = [cell.strip() for line in lines for cell in line.split(" | ") if cell.strip()]
    found: dict[str, str] = {}
    i = 0
    while i < len(tokens) - 1:
        field = _LABELS.get(tokens[i].lower())
        if field and field not in found and tokens[i + 1].lower() not in _LABELS:
            found[field] = tokens[i + 1]
            i += 2
        else:
            i += 1
    return found


def _find_title(lines: list[str]) -> str | None:
    first_label = next(
        (i for i, ln in enumerate(lines) if ln.split(" | ")[0].strip().lower() in _LABELS), None
    )
    if first_label is None:
        return None
    # Skip the company line at the top and the all-caps subtitle under the title.
    candidates = [ln for ln in lines[1:first_label] if not ln.isupper()]
    return " ".join(candidates) or None


def parse_metadata(lines: list[str]) -> tuple[DocumentMetadata, list[str]]:
    """Read the cover-page metadata table. Returns the metadata and any warnings."""
    found = _find_fields(lines)
    warnings: list[str] = []
    meta = DocumentMetadata(
        doc_code=found.get("doc_code"),
        title=_find_title(lines) if found else None,
        owner=found.get("owner"),
        classification=found.get("classification"),
    )

    if "department" in found:
        meta.department = canonical_department(found["department"])
        if meta.department is None:
            warnings.append("department_unrecognized")

    if "version" in found:
        match = _VERSION.match(found["version"])
        meta.version = match.group(1) if match else found["version"]

    if "effective_date" in found:
        meta.effective_date = _parse_date(found["effective_date"])
        if meta.effective_date is None:
            warnings.append("effective_date_unparsed")
    return meta, warnings
