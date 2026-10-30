import io
import shutil
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import Base, get_engine, get_session_factory
from app.main import create_app
from app.models import Document
from app.rag.vectorstore import close_all
from app.repositories import documents as repo

SAMPLE_DOCS = Path(__file__).resolve().parents[2] / "data" / "sample_docs"


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    yield Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        upload_dir=str(tmp_path / "uploads"),
        vector_store_path=str(tmp_path / "vectors"),
        embedding_provider="hashing",  # offline: no API key and no network in the normal suite
        max_upload_mb=1,
        max_files_per_request=3,
    )
    close_all()  # embedded Qdrant holds a lock on its folder until closed


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    Base.metadata.create_all(get_engine(settings.database_url))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    get_engine(settings.database_url).dispose()


@pytest.fixture
def session(settings: Settings) -> Iterator[Session]:
    Base.metadata.create_all(get_engine(settings.database_url))
    with get_session_factory(settings.database_url)() as db:
        yield db
    get_engine(settings.database_url).dispose()


@pytest.fixture
def sample_pdf() -> bytes:
    return (SAMPLE_DOCS / "01_Machine_Operation_Manual.pdf").read_bytes()


def make_docx(extra_names: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
        for name in extra_names:
            zf.writestr(name, "x")
    return buf.getvalue()


def add_stored_document(
    db: Session,
    settings: Settings,
    source: Path | bytes,
    *,
    ext: str = ".pdf",
    scope: str = "enterprise",
    department: str = "production",
    owner_id: str = "dev-user",
    name: str = "doc",
) -> Document:
    """Put a file into the upload folder and add its database row, as an upload would."""
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored = f"{name}{ext}"
    if isinstance(source, Path):
        shutil.copy(source, upload_dir / stored)
    else:
        (upload_dir / stored).write_bytes(source)
    document = Document(
        scope=scope,
        owner_id=owner_id,
        department=department,
        dedupe_key=f"{scope}:{department or owner_id}:{name}",
        original_filename=f"{name}{ext}",
        stored_filename=stored,
        file_ext=ext,
        mime_type="application/octet-stream",
        size_bytes=1,
        file_hash=f"hash-{name}-{scope}-{department}",
    )
    return repo.add(db, document)
