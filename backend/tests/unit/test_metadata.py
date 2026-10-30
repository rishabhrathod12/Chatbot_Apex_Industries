from datetime import date

from app.rag.ingestion.extractors.base import RawLine
from app.rag.ingestion.metadata import front_matter, parse_metadata

PDF_STYLE = [
    "Apex Precision Manufacturing Pvt. Ltd.",
    "Equipment Maintenance & Troubleshooting",
    "Manual",
    "CONTROLLED SAMPLE DOCUMENT FOR RAG CHATBOT TESTING",
    "Document ID",
    "APM-MNT-MAN-002",
    "Department",
    "Maintenance",
    "Version",
    "1.0 – Sample",
    "Effective date",
    "30 October 2026",
    "Document owner",
    "Maintenance Department",
    "Review status",
    "Training sample",
    "Classification",
    "Internal – Fictional",
    "Approval",
    "Not approved for real operations",
]


def test_parses_label_value_lines() -> None:
    meta, warnings = parse_metadata(PDF_STYLE)
    assert meta.doc_code == "APM-MNT-MAN-002"
    assert meta.department == "maintenance"
    assert meta.version == "1.0"
    assert meta.effective_date == date(2026, 10, 30)
    assert meta.owner == "Maintenance Department"
    assert meta.classification == "Internal – Fictional"
    assert warnings == []


def test_multi_line_title_skips_company_and_subtitle() -> None:
    meta, _ = parse_metadata(PDF_STYLE)
    assert meta.title == "Equipment Maintenance & Troubleshooting Manual"


def test_parses_pipe_separated_table_rows() -> None:
    meta, _ = parse_metadata(
        ["Acme", "Handbook", "Document ID | APM-HR-POL-009", "Department | HR", "Version | v2.1"]
    )
    assert meta.doc_code == "APM-HR-POL-009"
    assert meta.department == "hr"
    assert meta.version == "2.1"
    assert meta.title == "Handbook"


def test_department_alias_is_canonicalised() -> None:
    meta, _ = parse_metadata(["Document ID", "X-1", "Department", "Quality Control"])
    assert meta.department == "quality"


def test_unknown_department_gives_warning_not_error() -> None:
    meta, warnings = parse_metadata(["Document ID", "X-1", "Department", "Finance"])
    assert meta.department is None
    assert warnings == ["department_unrecognized"]


def test_date_formats_and_unparsable_date() -> None:
    assert parse_metadata(["Document ID", "X", "Effective date", "30-Oct-2026"])[
        0
    ].effective_date == date(2026, 10, 30)
    assert parse_metadata(["Document ID", "X", "Effective date", "2026-10-30"])[
        0
    ].effective_date == date(2026, 10, 30)
    meta, warnings = parse_metadata(["Document ID", "X", "Effective date", "sometime soon"])
    assert meta.effective_date is None
    assert warnings == ["effective_date_unparsed"]


def test_no_labels_means_empty_metadata() -> None:
    meta, warnings = parse_metadata(["Just some text", "and more"])
    assert meta.model_dump() == {
        "doc_code": None,
        "title": None,
        "department": None,
        "version": None,
        "effective_date": None,
        "owner": None,
        "classification": None,
    }
    assert warnings == []


def test_label_followed_by_label_is_not_taken_as_value() -> None:
    meta, _ = parse_metadata(["Document ID", "Department", "Safety"])
    assert meta.doc_code is None
    assert meta.department == "safety"


def test_front_matter_stops_at_first_heading() -> None:
    lines = [
        RawLine("Title"),
        RawLine("Document ID"),
        RawLine("X-1"),
        RawLine("Purpose"),
        RawLine("Department"),
    ]
    assert front_matter(lines) == ["Title", "Document ID", "X-1"]
