"""Guards against committing real secrets in files that git tracks."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_SECRET_NAME = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN)$", re.IGNORECASE)  # not CHUNK_MAX_TOKENS


def test_env_example_has_no_secret_values() -> None:
    """`.env.example` is committed, so its secret-looking settings must stay empty.
    Real values belong in `.env`, which git ignores."""
    offenders = []
    for number, line in enumerate(
        (ROOT / ".env.example").read_text(encoding="utf-8").splitlines(), 1
    ):
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.split(" #")[0].strip().strip("\"'")
        if _SECRET_NAME.search(name.strip()) and value:
            offenders.append(f"line {number}: {name.strip()}")
    assert not offenders, f"secret values in .env.example: {offenders}"


def test_env_file_is_git_ignored() -> None:
    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in patterns
    assert "!.env.example" in patterns


def test_docker_context_excludes_secrets_and_local_data() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for pattern in (".env", "storage/", "vector_db/", ".venv/", ".git/"):
        assert pattern in ignored, f"{pattern} would be copied into the Docker image"
    assert "!.env.example" in ignored  # the template is harmless; real .env files are excluded


def test_compose_file_holds_no_secrets_and_exposes_only_localhost() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "sk-" not in compose
    # Secrets come from .env, and compose refuses to start if they are missing.
    for name in ("POSTGRES_PASSWORD", "JWT_SECRET_KEY", "OPENAI_API_KEY"):
        assert f"${{{name}:?" in compose, f"{name} must be required from .env, not hard-coded"
    ports = [ln.strip() for ln in compose.splitlines() if re.match(r'\s*- "\d', ln)]
    assert ports and all(p.startswith('- "127.0.0.1:') for p in ports), ports
    assert "5432:5432" not in compose  # the database is never published


def test_dockerfile_does_not_run_as_root_or_copy_env_files() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^USER\s+(?!root)\S+", dockerfile, re.MULTILINE)
    assert ".env" not in re.sub(r"\.env\.example", "", dockerfile)
