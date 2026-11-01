# Project Status

**Project:** Manufacturing AI Knowledge Assistant, Apex Precision Manufacturing Pvt. Ltd.
**Last updated:** 2026-11-01
**Current state:** every milestone M0 to M14 has code, and so does the 9-phase authentication and interface redesign (below). All of it is on GitHub (`origin/main`). **The app now runs on PostgreSQL 18.6** (M10 verified on 2026-11-01). One milestone has still never run for real (M14 Docker), and **nobody has looked at the new web pages in a browser yet**.
**Ready to deploy?** Not yet. It is ready for a supervised pilot on one machine on a trusted network. Still open before other people use it: TLS, a stronger database password, scheduled off-machine backups (the backup and restore script itself is tested), a real-browser walkthrough, and removing the demo accounts (see "Known issues and gaps").

## Milestone tracker

| # | Milestone | Status |
|---|---|---|
| M0 | Architecture and requirements | Approved 2026-10-30 (pending questions remain, none blocked later milestones given defaults) |
| M1 | Project foundation and repo setup | Done, pushed |
| M2 | Backend foundation and configuration | Done, pushed |
| M3 | Document upload and storage (+ SQLAlchemy/Alembic) | Done, pushed |
| M4 | PDF/DOCX/TXT extraction and preprocessing | Done, pushed |
| M5 | Chunking and metadata management | Done, pushed |
| M6 | Embeddings and vector store integration (Qdrant, see ADR-0005) | Done, pushed |
| M7 | Basic RAG + first golden set | Done, pushed. Verified with real embeddings and `gpt-4.1-mini`; known weakness on near-miss questions, see `docs/retrieval-baseline.md` |
| M8 | Citations and conversational memory | Done, pushed. Follow-ups resolved 3/3 with the real model |
| M9 | Streamlit frontend | Rebuilt in the redesign (landing page with two portals, employee and Super Admin workspaces). **Not yet:** looked at in a real browser, polish for phones, an accessibility review |
| M10 | PostgreSQL support | **Done and verified on a real PostgreSQL 18.6 (native Windows install), 2026-11-01.** Scope: Postgres migration only. The 4 Postgres tests pass; the whole suite passes on PostgreSQL; the development data was copied across and checked cell by cell; the app runs on it. See `docs/postgres-setup.md` |
| M11 | Authentication, RBAC, department permissions | Done, pushed. Security report: `docs/security-test-report.md` |
| M12 | Hybrid search and reranking | Done, pushed. Measured against M7: `docs/retrieval-baseline.md` section 6 |
| M13 | RAGAS evaluation | Done and run for real on 2026-10-31 (separate `.venv-eval`). First baseline: faithfulness 0.91, answer relevancy 0.68 (probably misleading), context precision 0.98. The 8 lowest-faithfulness answers and one false refusal (H07) have **not** been read yet. See `docs/ragas-evaluation.md` |
| M14 | Docker, deployment, monitoring, hardening | Dockerfile, compose, `/health/ready`, security headers written. **Docker never built or run** (not installed). Not done: TLS, CI, metrics, backups, more than one API worker. See `docs/deployment.md` |

## Authentication and interface redesign (9 phases)

| Phase | What it added | State |
|---|---|---|
| 1 | Login IDs (migration 0005), first-time setup form for the first Super Admin | Pushed |
| 2 | Temporary passwords shown once, forced password change, change-password endpoint | Pushed |
| 3 | Portal-aware login (the server refuses a mismatch; the portal never grants anything), last Super Admin protection | Pushed |
| 4 | Admin stats and audit log endpoints, split audit actions (migration 0006) | Pushed |
| 5 | Company document details, archive and restore (migration 0007) | Pushed |
| 6 | New document versions with no search gap, redaction of old citations (migration 0008) | Pushed |
| 7 | Landing page with Employee and Super Admin cards, sign-in screens | Pushed |
| 8 | Employee and Super Admin workspaces, PDF-only personal uploads | Pushed |
| 9 | Final authorization sweep (111 tests, 11 deliberate-break checks all caught), documents brought up to date | Pushed |

Decisions made by default because they were not answered, all easy to change: department managers keep their ability to manage their own departments' documents; new Login IDs are made from the name; the six departments are fixed in code (a read-only Departments page); the Super Admin keeps a page for asking the company documents.

## Environment facts (checked 2026-11-01)

- Git repo on `main` (`https://github.com/rishabhrathod12/Chatbot_Apex_Industries.git`).
- App environment: `.venv/`, Python 3.12.10. RAGAS environment: `.venv-eval/` (kept separate because ragas downgrades `openai`). The old `apex/` venv (Python 3.14.5) is unused.
- **Database:** PostgreSQL 18.6 installed natively (service `postgresql-x64-18`, port 5432). Role `apex`, databases `apex` (the app's) and `apex_test` (scratch, wiped by the test suite). The app uses it through `DATABASE_URL` in `.env`. Removing that line returns the app to SQLite.
- Docker is **not installed**. An `OPENAI_API_KEY` is set in `.env` (never committed).
- `storage/app.db` (SQLite) holds the data as it was when the app was switched on 2026-11-01 and is no longer written to. `storage/app.db.before-postgres.bak` is an identical backup. Anything done in the app after the switch exists only in PostgreSQL.
- Database migrations: 8 (0001 to 0008), applied to PostgreSQL `apex` and to the SQLite file.
- 6 sample PDFs in `data/sample_docs/` (fictional).
- Test suite on SQLite (last full run, 2026-10-31): 1168 passed, 4 skipped (the Postgres tests), 2 xfailed (M6 offline-embedder misses), 1 deselected (live API). Ruff and mypy clean.
- Test suite on PostgreSQL (2026-11-01, `apex_test`): 1171 passed, 2 xfailed, 1 deselected, and 1 failed. The failure was a mistake in the copy test, not in the application (it compared times without converting the zone). After the fix that file passes 4 of 4 with either database as the source. The full suite was not rerun after that fix.

## Open decisions (need user approval)

See `docs/decisions/ADR-0001-initial-decisions.md`, section "Pending".

## Known issues and gaps

- An old copy of the API or web page left running from an earlier session answers with old code. `scripts\dev.ps1` now refuses to start over it (use `-Restart`), and the page reports an out-of-date API (`api_level` in `GET /health`) instead of failing at sign-in with `unknown_error`.
- M14 (Docker) has never run against the real thing. Expect small first-run fixes. Note that `docker-compose.yml` uses PostgreSQL 16, while the native install is 18.6.
- PostgreSQL returns times in the server's time zone (India time on this machine), not UTC. Nothing in the application broke, but anything new that strips the time zone will show the wrong hour.
- The `apex` database password is short. Choose a strong one before the database holds real data (`ALTER ROLE apex PASSWORD '...'`, then update `.env`).
- The demo accounts (`admin` with a lost password, `production.employee`, and similar) should be disabled or removed before real staff use the app.
- Backup and restore of PostgreSQL, `storage/uploads/` and `vector_db/` exist and were tested on 2026-11-01 (`scripts/backup.py`, `docs/deployment.md`). Still missing: a schedule, and a copy kept on another disk or machine. A full recovery of the live database was not rehearsed; the restore into the scratch database was.
- The new web pages were only tested with Streamlit's test runner: no real-browser check of layout, spacing, the pop-ups or the 3 to 4 second auto-refresh of document lists.
- Old conversations: citations are blanked when access is lost, but the stored answer text is not rewritten.
- No TLS in the Docker setup: do not expose it to other people without a reverse proxy.
- One API process only: the embedded Qdrant store, the in-memory login limiter and the last-Super-Admin lock do not support several.
- Login rate limiting sees the web container's address when run in Docker, so it limits per email only.
- No CI pipeline, metrics or alerting, and no automated or off-machine backups.
- Keyword search scans chunks in Python (fine for thousands of chunks, not millions); see `docs/retrieval-baseline.md`.
- Document details (title, version, type) cannot be edited after upload; upload a new version instead.
- `frontend/package-lock.json` is untracked and looks like a stray file.

## Change log

- 2026-10-30: M0 documents drafted. M1 and M2 built.
- 2026-10-31: M3 to M9 built (upload, extraction, chunking, vector store, RAG, citations, Streamlit interface).
- 2026-10-31: M11 (login, roles, permissions), M12 (hybrid search, reranking), M13 (RAGAS, offline), M10 (PostgreSQL support, unverified) and M14 (Docker, readiness check, security headers, unverified). All pushed to GitHub.
- 2026-10-31: status file brought up to date (it still described the 2026-10-30 state with no code).
- 2026-10-31: M13 run for real (first RAGAS baseline).
- 2026-10-31: authentication and interface redesign, phases 1 to 8, pushed. Phase 9 (authorization sweep, documents) pushed too.
- 2026-10-31: repository cleanup on branch `cleanup/structure` (phases 0 to 2): dead code removed, shared model helpers, the 14 security test files moved to `backend/tests/security/`. Behaviour unchanged; same 1168 tests pass.
- 2026-10-31: `docs/data-model.md` and `docs/architecture.md` rewritten to match the code and migration 0008 (they still described the original M0 design).
- 2026-11-01: **M10 verified on PostgreSQL 18.6.** Migrations 0001 to 0008 run up, down and up again; the migrated schema matches the models; the whole suite passes on PostgreSQL; the development data (9 users, 8 documents, 10 conversations, 38 messages, 23 refresh tokens, 61 audit rows) was copied and compared cell by cell; the app was switched to PostgreSQL. Two mistakes in `test_postgres.py` were fixed (no application code changed).
- 2026-11-01: `scripts/backup.py` added (database, uploads and vector index into a dated folder; restore into a scratch database with row-count comparison; damaged files refused). Tested with a real backup of the live database.
