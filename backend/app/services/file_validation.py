import codecs
import zipfile
from pathlib import Path

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}
MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
_CHUNK = 64 * 1024


class UploadRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def sanitize_filename(name: str | None) -> str:
    """Keep only a display-safe base name. It is never used to build a path on disk."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    return base[:255] or "unnamed"


def get_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadRejected("unsupported_type")
    return ext


def validate_content(path: Path, ext: str) -> None:
    """Check the file's real content matches its extension (not just its name)."""
    if ext == ".pdf":
        with path.open("rb") as f:
            if f.read(5) != b"%PDF-":
                raise UploadRejected("content_mismatch")
    elif ext == ".docx":
        _validate_docx(path)
    elif ext == ".txt":
        _validate_text(path)


def _validate_docx(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise UploadRejected("content_mismatch")
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise UploadRejected("content_mismatch")
            if sum(i.file_size for i in zf.infolist()) > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise UploadRejected("file_too_large")
    except zipfile.BadZipFile as exc:
        raise UploadRejected("content_mismatch") from exc


def _validate_text(path: Path) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            if b"\x00" in chunk:
                raise UploadRejected("content_mismatch")
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError as exc:
                raise UploadRejected("content_mismatch") from exc
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise UploadRejected("content_mismatch") from exc
