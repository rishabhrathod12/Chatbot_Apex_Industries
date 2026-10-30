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
