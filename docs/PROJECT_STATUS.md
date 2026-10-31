# Project Status

**Project:** Manufacturing AI Knowledge Assistant, Apex Precision Manufacturing Pvt. Ltd.
**Last updated:** 2026-10-31
**Current state:** every milestone M0 to M14 has code, and so does the 9-phase authentication and interface redesign (below). All nine redesign phases are on GitHub (`origin/main`). Two milestones still have never run for real (M10 Postgres, M14 Docker), and **nobody has looked at the new web pages in a browser yet**.

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
| M10 | PostgreSQL support | Scope chosen: Postgres migration only. Code and tests written, **not verified on a real PostgreSQL** (none installed). The 4 Postgres tests are skipped until `TEST_POSTGRES_URL` is set. See `docs/postgres-setup.md` |
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

## Environment facts (checked 2026-10-31)

- Git repo on `main` (`https://github.com/rishabhrathod12/Chatbot_Apex_Industries.git`).
- App environment: `.venv/`, Python 3.12.10. RAGAS environment: `.venv-eval/` (kept separate because ragas downgrades `openai`). The old `apex/` venv (Python 3.14.5) is unused.
- Docker and PostgreSQL are **not installed**. An `OPENAI_API_KEY` is set in `.env` (never committed).
- Database migrations: 8 (0001 to 0008), applied to the development database.
- 6 sample PDFs in `data/sample_docs/` (fictional).
- Test suite: 1145 passed, 4 skipped (Postgres), 2 xfailed (M6 offline-embedder misses), 1 deselected (live API). Ruff and mypy clean.

## Open decisions (need user approval)

See `docs/decisions/ADR-0001-initial-decisions.md`, section "Pending".

## Known issues and gaps

- M10 and M14 have never run against the real thing (Postgres, Docker). Expect small first-run fixes.
- The new web pages were only tested with Streamlit's test runner: no real-browser check of layout, spacing, the pop-ups or the 3 to 4 second auto-refresh of document lists.
- Old conversations: citations are blanked when access is lost, but the stored answer text is not rewritten.
- No TLS in the Docker setup: do not expose it to other people without a reverse proxy.
- One API process only: the embedded Qdrant store, the in-memory login limiter and the last-Super-Admin lock do not support several.
- Login rate limiting sees the web container's address when run in Docker, so it limits per email only.
- No CI pipeline, metrics or alerting, and no automated or tested backups.
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
