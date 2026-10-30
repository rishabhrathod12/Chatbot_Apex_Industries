import re
from pathlib import Path

import docx
import pymupdf
import pytest

from app.rag.ingestion.extractors import ExtractionError, extract
from app.schemas.extraction import ExtractedDocument
from tests.conftest import SAMPLE_DOCS

# file, doc code, canonical department, title, a sentence that sits on page 2
SAMPLES = [
    (
        "01_Machine_Operation_Manual.pdf",
        "APM-PROD-MAN-001",
        "production",
        "CNC Machine Operation Manual",
        "Keep doors and guards closed during automatic operation.",
    ),
    (
        "02_Maintenance_Manual.pdf",
        "APM-MNT-MAN-002",
        "maintenance",
        "Equipment Maintenance & Troubleshooting Manual",
        "Do not repeatedly reset alarms or restart equipment to keep production moving.",
    ),
    (
        "03_Factory_Safety_Manual.pdf",
        "APM-HSE-MAN-003",
        "safety",
        "Factory Health & Safety Manual",
        "Never bypass guards, interlocks, light curtains, or emergency-stop devices.",
    ),
    (
        "04_Production_SOP.pdf",
        "APM-PROD-SOP-004",
        "production",
        "Production Planning & Process Control SOP",
        "Do not continue batch production until required first-piece approval is recorded.",
    ),
    (
        "05_HR_Policy.pdf",
        "APM-HR-POL-005",
        "hr",
        "Employee Attendance & Leave Policy",
        "Employees must not record attendance on behalf of another person.",
    ),
    (
        "06_Quality_Control_SOP.pdf",
        "APM-QLT-SOP-006",
        "quality",
        "Quality Control & Product Inspection SOP",
        "Only authorized Quality personnel may release product when the defined acceptance "
        "criteria are met.",
    ),
]


def _sample(name: str) -> ExtractedDocument:
    return extract(SAMPLE_DOCS / name, ".pdf")


@pytest.mark.parametrize(("name", "code", "dept", "title", "sentence"), SAMPLES)
def test_sample_pdf_metadata(name: str, code: str, dept: str, title: str, sentence: str) -> None:
    doc = _sample(name)
    assert doc.metadata.doc_code == code
    assert doc.metadata.department == dept
    assert doc.metadata.title == title
    assert doc.metadata.version == "1.0"
    assert str(doc.metadata.effective_date) == "2026-10-30"
    assert doc.warnings == []


@pytest.mark.parametrize(("name", "code", "dept", "title", "sentence"), SAMPLES)
def test_sample_pdf_pages_and_header_footer_removed(
    name: str, code: str, dept: str, title: str, sentence: str
) -> None:
    doc = _sample(name)
    assert [p.number for p in doc.pages] == [1, 2, 3]
    for page in doc.pages:
        assert "NOT FOR OPERATIONAL USE" not in page.text
        assert not re.search(r"(?m)^Page \d+$", page.text)


@pytest.mark.parametrize(("name", "code", "dept", "title", "sentence"), SAMPLES)
def test_sample_pdf_sentence_is_on_the_right_page(
    name: str, code: str, dept: str, title: str, sentence: str
) -> None:
    doc = _sample(name)
    where = [p.number for p in doc.pages if sentence in p.text]
    assert where == [2]


@pytest.mark.parametrize(("name", "code", "dept", "title", "sentence"), SAMPLES)
def test_sample_pdf_block_kinds(name: str, code: str, dept: str, title: str, sentence: str) -> None:
    doc = _sample(name)
    page1, page2, page3 = doc.pages

    kinds1 = [b.kind for b in page1.blocks]
    assert kinds1 == ["title_block", "boilerplate", "body", "boilerplate"]
    assert code in page1.blocks[0].text  # the document ID stays searchable
    assert "fictional sample content" in page1.blocks[1].text
    assert page1.blocks[2].section_title == "Purpose"

    assert {b.kind for b in page2.blocks} == {"body"}
    assert all(b.section_title and b.section_title[0].isdigit() for b in page2.blocks)

    assert [b.kind for b in page3.blocks] == ["revision_history"]
    assert page3.blocks[0].section_title == "Revision history"


def test_extraction_is_deterministic() -> None:
    first = _sample("03_Factory_Safety_Manual.pdf").model_dump()
    second = _sample("03_Factory_Safety_Manual.pdf").model_dump()
    assert first == second


def test_wrapped_bullet_is_reflowed_into_one_line() -> None:
    doc = _sample("03_Factory_Safety_Manual.pdf")
    text = doc.pages[1].text
    # In the PDF this bullet wraps over two lines.
    assert (
        "- Maintenance and servicing involving hazardous energy must be performed under the "
        "site's approved lockout/tagout or energy-isolation procedure by authorized personnel."
    ) in text


# ---------- synthetic PDFs ----------


def _make_pdf(path: Path, pages: list[str], header: str = "Acme Corp | Confidential") -> None:
    doc = pymupdf.open()
    for number, body in enumerate(pages, start=1):
        page = doc.new_page()
        page.insert_text((72, 40), header)
        page.insert_text((72, 56), f"Page {number}")
        y = 100
        for line in body.splitlines():
            page.insert_text((72, y), line)
            y += 16
        page.insert_text((72, 800), f"Acme footer {number}")
    doc.save(str(path))
    doc.close()


def test_synthetic_pdf_removes_running_header_and_footer(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    _make_pdf(path, ["Alpha one\nAlpha two", "Beta one\nBeta two", "Gamma one\nGamma two"])
    doc = extract(path, ".pdf")
    assert [p.number for p in doc.pages] == [1, 2, 3]
    for page in doc.pages:
        assert "Acme" not in page.text
        assert "Page " not in page.text
    assert "Beta one" in doc.pages[1].text
    assert doc.metadata.doc_code is None
    assert doc.pages[0].blocks[0].kind == "body"  # no cover table, so no title block


def test_empty_file_is_reported_as_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    with pytest.raises(ExtractionError) as exc:
        extract(path, ".pdf")
    assert exc.value.reason == "corrupt_pdf"


def test_garbage_bytes_are_reported_as_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.7\nthis is not really a pdf at all")
    with pytest.raises(ExtractionError) as exc:
        extract(path, ".pdf")
    assert exc.value.reason == "corrupt_pdf"


def test_encrypted_pdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "secret")
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    doc.close()
    with pytest.raises(ExtractionError) as exc:
        extract(path, ".pdf")
    assert exc.value.reason == "encrypted_pdf"


def test_pdf_without_text_layer_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(str(path))
    doc.close()
    with pytest.raises(ExtractionError) as exc:
        extract(path, ".pdf")
    assert exc.value.reason == "no_text_layer"


def test_page_cap_is_enforced() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract(SAMPLE_DOCS / "01_Machine_Operation_Manual.pdf", ".pdf", max_pages=2)
    assert exc.value.reason == "too_many_pages"


def test_unsupported_extension_raises() -> None:
    with pytest.raises(ExtractionError) as exc:
        extract(SAMPLE_DOCS / "01_Machine_Operation_Manual.pdf", ".exe")
    assert exc.value.reason == "unsupported_type"


# ---------- DOCX ----------


def test_docx_headings_paragraphs_and_tables(tmp_path: Path) -> None:
    path = tmp_path / "policy.docx"
    document = docx.Document()
    document.add_heading("Safety Rules", level=1)
    document.add_paragraph("Wear PPE at all times.")
    document.add_heading("Records", level=1)
    table = document.add_table(rows=2, cols=2)
    for r, row in enumerate([["Item", "Owner"], ["Log", "Safety"]]):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    document.save(str(path))

    doc = extract(path, ".docx")
    assert len(doc.pages) == 1
    assert doc.pages[0].number is None
    blocks = doc.pages[0].blocks
    assert [(b.section_title, b.kind) for b in blocks] == [
        ("Safety Rules", "body"),
        ("Records", "body"),
    ]
    assert blocks[0].text == "Wear PPE at all times."
    assert blocks[1].text == "Item | Owner\nLog | Safety"


def test_docx_cover_table_metadata(tmp_path: Path) -> None:
    path = tmp_path / "cover.docx"
    document = docx.Document()
    document.add_paragraph("Apex Precision Manufacturing Pvt. Ltd.")
    document.add_paragraph("Stores Handling Guide")
    table = document.add_table(rows=2, cols=2)
    for r, (label, value) in enumerate(
        [("Document ID", "APM-STR-GDE-007"), ("Department", "Stores")]
    ):
        table.cell(r, 0).text = label
        table.cell(r, 1).text = value
    document.add_heading("Purpose", level=1)
    document.add_paragraph("Explain how stores issue material.")
    document.save(str(path))

    doc = extract(path, ".docx")
    assert doc.metadata.doc_code == "APM-STR-GDE-007"
    assert doc.metadata.department == "stores"
    assert doc.metadata.title == "Stores Handling Guide"
    assert [b.kind for b in doc.pages[0].blocks] == ["title_block", "body"]


def test_empty_docx_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.docx"
    docx.Document().save(str(path))
    with pytest.raises(ExtractionError) as exc:
        extract(path, ".docx")
    assert exc.value.reason == "empty_document"


def test_non_docx_bytes_reported_as_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "fake.docx"
    path.write_bytes(b"not a zip")
    with pytest.raises(ExtractionError) as exc:
        extract(path, ".docx")
    assert exc.value.reason == "corrupt_docx"


# ---------- TXT ----------


def test_txt_sections_without_reflow(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes("﻿1. Scope\nline one\nline two\n\n2. Records\nlog everything\n".encode())
    doc = extract(path, ".txt")
    assert doc.pages[0].number is None
    blocks = doc.pages[0].blocks
    assert [(b.section_title, b.text) for b in blocks] == [
        ("1. Scope", "line one\nline two"),
        ("2. Records", "log everything"),
    ]


def test_txt_invalid_encoding_and_empty(tmp_path: Path) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\xfa")
    with pytest.raises(ExtractionError) as exc:
        extract(bad, ".txt")
    assert exc.value.reason == "invalid_encoding"

    empty = tmp_path / "empty.txt"
    empty.write_text("   \n\n  ")
    with pytest.raises(ExtractionError) as exc2:
        extract(empty, ".txt")
    assert exc2.value.reason == "empty_document"
