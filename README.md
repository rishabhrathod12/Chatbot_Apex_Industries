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
(`alembic -c backend/alembic.ini upgrade head`), then create your Super Admin. Easiest: start the app (`.\scripts\dev.ps1`); on a system with no accounts the sign-in page
shows a **First-time setup** form, and you create the Super Admin there (it disappears once the account exists; in production it
needs `SETUP_TOKEN` in `.env`, see `docs/security.md`). Or on the command line, with the password typed at a hidden prompt:

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

`dev.ps1` refuses to start if ports 8000 or 8501 are already taken (an old copy of the app still running would
keep answering with old code and sign-in would fail with confusing errors). Close the old windows, or run
`.\scripts\dev.ps1 -Restart` to stop them and start fresh. The web page also checks the API's version and says so
plainly when the API is an older copy.

**Lost or exposed password?** On the computer running the app: `.venv\Scripts\python.exe -m app.cli reset-password --login-id <their Login ID or email>`.
It ends all their sessions, prints a one-time password once, and makes them choose their own at the next sign-in.
Run it in your own terminal and do not paste or screenshot its output.

The first page offers two sign-in options, **Employee Login** and **Super Admin Login** (a bookmark such as
`http://localhost:8501/?portal=admin` opens one directly). Both ask for a Login ID (an email works too) and a password. The
choice is only a convenience: the server checks the account's real role and refuses an account that belongs to the other
portal. Someone given a temporary password is asked to choose their own before anything else. After signing in you see a workspace that matches your account:

- **Employees** (and department managers): Dashboard, Upload PDF, My Documents, Chat with My Documents, Company
  Knowledge (ask, or browse the company documents you may read) and Conversation History. Staff add their own
  documents as PDFs only. Managers also get Department Documents for their departments.
- **Super Admins**: Overview (account, department and document numbers, recent activity), Employee Management,
  Create Employee, Company Documents (details, archive/restore, new versions), Upload Company Documents,
  Departments and Audit Logs.

Sign in, then (as a Super Admin) open **Upload Company Documents**, click *Load the 6 sample documents*, wait for each to show **Ready**, and
try **Ask Company Documents**. What each person can see depends on their role and departments (see `docs/security.md`). Only one API process can use the vector folder at a time, so close an old API
window before starting another. Set `API_BASE_URL` if the API runs somewhere else.

Links work as bookmarks: `http://localhost:8501/?page=documents` opens the library and
`http://localhost:8501/?c=<conversation id>` opens a saved chat.

## Docker

`docker compose up -d --build` starts PostgreSQL, the API and the web interface. Required `.env` values, first
admin, health checks and what is still missing before real users (TLS above all): `docs/deployment.md`.

## Database

```powershell
cd backend
alembic upgrade head        # creates storage/app.db (SQLite) by default
uvicorn app.main:app --reload
```

SQLite is the default. To use PostgreSQL set `DATABASE_URL=postgresql://...` in `.env`; install, data copy and
testing steps are in `docs/postgres-setup.md`.

## Embeddings

Set `OPENAI_API_KEY` in `.env` to embed with OpenAI (`text-embedding-3-small`). To run fully offline
without a key, set `EMBEDDING_PROVIDER=hashing` (development only, no real semantic search).
Without either, uploads succeed but the document is marked `failed: embedding_not_configured`;
fix the setting and call `POST /api/v1/documents/{id}/reindex`.

## How answers are found

Each question is searched two ways, by meaning and by keywords (exact words, ids, acronyms), and the
results are merged. A second check then scores each candidate passage on whether it actually
*answers* the question, and drops the ones that are only related. Both steps only ever see documents
the person is allowed to read. They can be switched off with `RETRIEVAL_MODE=semantic` and
`RERANK_ENABLED=false` (then set `RETRIEVAL_MIN_SCORE` back to about 0.40). Details and measurements:
`docs/retrieval-baseline.md`.

## Asking questions and measuring retrieval

`POST /api/v1/chat` with `{"question": "..."}` returns an answer, its sources (document, section, page,
and a verbatim quote) or a "not found" result, plus a `conversation_id`. Send that id back to ask a
follow-up. `GET /api/v1/conversations` lists your conversations. It needs `OPENAI_API_KEY` (embeddings and chat). Retrieval quality on the
sample documents: `python -m eval.run --provider openai --mode both`; compare answer settings with `python -m eval.compare` (see `docs/retrieval-baseline.md`).

## RAGAS scoring (faithfulness, relevancy, context precision)

Runs in a separate environment so it can't disturb the app's packages. Setup, cost and limits:
`docs/ragas-evaluation.md`.

```powershell
py -3.12 -m venv .venv-eval
.venv-eval\Scripts\python.exe -m pip install -e ".[eval]"
.venv-eval\Scripts\python.exe -m eval.ragas_run --limit 5     # needs OPENAI_API_KEY
```

## Quality checks

```powershell
ruff check .
mypy
pytest                      # offline, no API key needed
pytest -m live              # optional: calls the real OpenAI API (needs OPENAI_API_KEY)
```
