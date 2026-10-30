# Apex Knowledge Assistant

Enterprise RAG application for the fictional company **Apex Precision Manufacturing Pvt. Ltd.**
A supervised learning and portfolio project, built milestone by milestone.

Status: see [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md). Design: [docs/architecture.md](docs/architecture.md).

All sample documents in `data/sample_docs/` are fictional and not for operational use.

## Setup (Windows, Python 3.12)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env      # then fill in values (never commit .env)
pre-commit install
```

## Database

```powershell
cd backend
alembic upgrade head        # creates storage/app.db (SQLite) by default
uvicorn app.main:app --reload
```

## Quality checks

```powershell
ruff check .
mypy
pytest
```
