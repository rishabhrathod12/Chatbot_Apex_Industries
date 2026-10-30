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

## Embeddings

Set `OPENAI_API_KEY` in `.env` to embed with OpenAI (`text-embedding-3-small`). To run fully offline
without a key, set `EMBEDDING_PROVIDER=hashing` (development only, no real semantic search).
Without either, uploads succeed but the document is marked `failed: embedding_not_configured`;
fix the setting and call `POST /api/v1/documents/{id}/reindex`.

## Asking questions and measuring retrieval

`POST /api/v1/chat` with `{"question": "..."}` returns an answer, its sources (document, section, page)
or a "not found" result. It needs `OPENAI_API_KEY` (embeddings and chat). Retrieval quality on the
sample documents: `python -m eval.run --provider openai` (see `docs/retrieval-baseline.md`).

## Quality checks

```powershell
ruff check .
mypy
pytest                      # offline, no API key needed
pytest -m live              # optional: calls the real OpenAI API (needs OPENAI_API_KEY)
```
