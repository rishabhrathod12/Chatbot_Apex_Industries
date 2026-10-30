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
copy .env.example .env      # then put real values (like OPENAI_API_KEY) in .env ONLY.
                            # Never put a key in .env.example: git tracks that file.
pre-commit install
```

## First run: create accounts

There is no default account. Put a signing secret in `.env` (at least 32 characters, for example the output of
`python -c "import secrets; print(secrets.token_urlsafe(48))"`) as `JWT_SECRET_KEY=...`, apply the database
(`alembic -c backend/alembic.ini upgrade head`), then create your Super Admin. The password is typed at a hidden prompt:

```powershell
.venv\Scripts\python.exe -m app.cli create-user --email you@company.com --name "Your Name" --role super_admin
```

For a development demo, `python -m app.cli seed-demo` creates six users (an admin, three managers, two employees)
that share one generated password, printed once. It refuses to run when `APP_ENV=production`.

## Web interface

One command starts the API and the web interface and opens the browser:

```powershell
.\scripts\dev.ps1          # web interface http://localhost:8501, API docs http://localhost:8000/docs
```

Sign in, then (as a Super Admin) open **Documents**, click *Load the 6 sample documents*, wait for each to show **Ready**, and
go to **Chat**. What each person can see depends on their role and departments (see `docs/security.md`). Only one API process can use the vector folder at a time, so close an old API
window before starting another. Set `API_BASE_URL` if the API runs somewhere else.

Links work as bookmarks: `http://localhost:8501/?page=documents` opens the library and
`http://localhost:8501/?c=<conversation id>` opens a saved chat.

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

`POST /api/v1/chat` with `{"question": "..."}` returns an answer, its sources (document, section, page,
and a verbatim quote) or a "not found" result, plus a `conversation_id`. Send that id back to ask a
follow-up. `GET /api/v1/conversations` lists your conversations. It needs `OPENAI_API_KEY` (embeddings and chat). Retrieval quality on the
sample documents: `python -m eval.run --provider openai` (see `docs/retrieval-baseline.md`).

## Quality checks

```powershell
ruff check .
mypy
pytest                      # offline, no API key needed
pytest -m live              # optional: calls the real OpenAI API (needs OPENAI_API_KEY)
```
