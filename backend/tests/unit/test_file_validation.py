import io
import zipfile
from pathlib import Path

import pytest

from app.services.file_validation import (
    UploadRejected,
    get_extension,
    sanitize_filename,
    validate_content,
)
from tests.conftest import make_docx


def _write(tmp_path: Path, data: bytes) -> Path:
    p = tmp_path / "f"
    p.write_bytes(data)
    return p


def test_sanitize_strips_directories() -> None:
    assert sanitize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert sanitize_filename("C:\\Users\\x\\report.pdf") == "report.pdf"
    assert sanitize_filename("") == "unnamed"
    assert sanitize_filename(None) == "unnamed"


@pytest.mark.parametrize("name", ["a.PDF", "b.docx", "c.txt"])
def test_allowed_extensions(name: str) -> None:
    assert get_extension(name).startswith(".")


@pytest.mark.parametrize("name", ["a.exe", "b.pdf.exe", "noext", "c.doc"])
def test_disallowed_extensions(name: str) -> None:
    with pytest.raises(UploadRejected) as e:
        get_extension(name)
    assert e.value.reason == "unsupported_type"


def test_pdf_accepts_real_header(tmp_path: Path) -> None:
    validate_content(_write(tmp_path, b"%PDF-1.7\nrest"), ".pdf")


def test_exe_renamed_to_pdf_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadRejected) as e:
        validate_content(_write(tmp_path, b"MZ\x90\x00 fake exe"), ".pdf")
    assert e.value.reason == "content_mismatch"


def test_docx_accepts_valid(tmp_path: Path) -> None:
    validate_content(_write(tmp_path, make_docx()), ".docx")


def test_zip_without_word_document_rejected(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(UploadRejected) as e:
        validate_content(_write(tmp_path, buf.getvalue()), ".docx")
    assert e.value.reason == "content_mismatch"


def test_non_zip_docx_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadRejected):
        validate_content(_write(tmp_path, b"not a zip"), ".docx")


def test_txt_accepts_utf8(tmp_path: Path) -> None:
    validate_content(_write(tmp_path, "héllo wörld".encode()), ".txt")


def test_binary_txt_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadRejected):
        validate_content(_write(tmp_path, b"abc\x00def"), ".txt")


def test_invalid_utf8_txt_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadRejected):
        validate_content(_write(tmp_path, b"\xff\xfe\xfa"), ".txt")
