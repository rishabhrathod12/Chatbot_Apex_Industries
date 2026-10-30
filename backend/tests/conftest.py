import io
import shutil
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal
from app.core.config import Settings, get_settings
from app.core.database import Base, get_engine, get_session_factory
from app.core.permissions import Principal, Role
from app.core.rate_limit import LoginLimiter
from app.main import create_app
from app.models import Document
from app.rag.generation.llm import ChatMessage, LlmError
from app.rag.ingestion.embedder import HashingEmbedder
from app.rag.vectorstore import QdrantVectorStore, close_client
from app.repositories import documents as repo
from app.schemas.auth import UserCreate
from app.services import users as user_service
from eval.indexing import index_sample_documents

SAMPLE_DOCS = Path(__file__).resolve().parents[2] / "data" / "sample_docs"


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    value = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        upload_dir=str(tmp_path / "uploads"),
        vector_store_path=str(tmp_path / "vectors"),
        embedding_provider="hashing",  # offline: no API key and no network in the normal suite
        max_upload_mb=1,
        max_files_per_request=3,
        jwt_secret_key="test-secret-" + "x" * 32,
        password_hash_cost_log2=10,  # fast hashing for tests (production uses 15)
        login_max_failures=3,
    )
    yield value
    close_client(value.vector_store_path)  # embedded Qdrant locks its folder until closed


@pytest.fixture(autouse=True)
def _no_ambient_api_key(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the normal suite offline even if a real key is set in the shell."""
    if "live" not in request.keywords:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@dataclass
class IndexedSamples:
    store: QdrantVectorStore
    embedder: HashingEmbedder


@pytest.fixture(scope="session")
def indexed_samples(tmp_path_factory: pytest.TempPathFactory) -> Iterator[IndexedSamples]:
    """The six sample PDFs, indexed once for the whole test session (read-only for tests)."""
    path = str(tmp_path_factory.mktemp("sample_index"))
    embedder = HashingEmbedder()
    store = index_sample_documents(path, embedder, SAMPLE_DOCS)
    yield IndexedSamples(store, embedder)
    close_client(path)


@dataclass
class ScriptedChat:
    """A stand-in language model that returns canned replies and records what it was sent."""

    replies: list[str] | str = "ok"
    error: Exception | None = None
    fail_on_calls: set[int] = field(default_factory=set)  # 0-based call numbers that fail
    calls: list[list[ChatMessage]] = field(default_factory=list)

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        if len(self.calls) - 1 in self.fail_on_calls:
            if isinstance(self.replies, list):
                self.replies.pop(0)
            raise LlmError("llm_request_failed")
        if isinstance(self.replies, str):
            return self.replies
        return self.replies.pop(0)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    Base.metadata.create_all(get_engine(settings.database_url))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.login_limiter = LoginLimiter(
        settings.login_max_failures, settings.login_window_minutes * 60
    )
    # Most tests are about features, not login, so they act as a logged-in Super Admin.
    # tests/integration/test_auth*.py use real tokens instead (see `raw_client`).
    app.dependency_overrides[get_current_principal] = lambda: act_as_principal("dev-user")
    yield TestClient(app)
    get_engine(settings.database_url).dispose()


def act_as_principal(
    user_id: str,
    role: Role = Role.SUPER_ADMIN,
    departments: frozenset[str] = frozenset(),
) -> Principal:
    return Principal(
        user_id=user_id,
        email=f"{user_id}@example.test",
        full_name=user_id,
        role=role,
        departments=departments,
    )


@pytest.fixture
def raw_client(settings: Settings) -> Iterator[TestClient]:
    """The real app with real authentication (no overrides except the test settings)."""
    Base.metadata.create_all(get_engine(settings.database_url))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.login_limiter = LoginLimiter(
        settings.login_max_failures, settings.login_window_minutes * 60
    )
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


PASSWORD = "Correct-Horse-Battery-9"


def add_user(
    session: Session,
    settings: Settings,
    email: str,
    role: Role = Role.EMPLOYEE,
    departments: list[str] | None = None,
    password: str = PASSWORD,
):  # noqa: ANN201
    """Create a real user in the test database."""
    data = UserCreate(
        email=email,
        full_name=email.split("@")[0],
        password=password,
        role=role,
        departments=departments or [],
    )
    return user_service.create_user(session, settings, None, data)


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    """Log in through the real endpoint and return the Authorization header for that user."""
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
