import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.database import Base, get_engine
from app.main import create_app
from app.models import Document  # noqa: F401  (registers the table)

SAMPLE_DOCS = Path(__file__).resolve().parents[2] / "data" / "sample_docs"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        upload_dir=str(tmp_path / "uploads"),
        max_upload_mb=1,
        max_files_per_request=3,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    Base.metadata.create_all(get_engine(settings.database_url))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
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
