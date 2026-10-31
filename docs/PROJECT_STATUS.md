# Project Status

**Project:** Manufacturing AI Knowledge Assistant, Apex Precision Manufacturing Pvt. Ltd.
**Last updated:** 2026-10-31
**Current state:** every milestone M0 to M14 has code and is on GitHub (`origin/main`). Three are not yet verified in a real environment (M10, M13, M14: see the tracker). M9 still lacks phone polish and an accessibility review.

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
| M9 | Streamlit frontend | Chat, sources, conversations, documents and login work. **Not yet:** polish for phones, accessibility review |
| M10 | PostgreSQL support | Scope chosen: Postgres migration only (no versions, recycle bin or library filters). Code and tests written, **not verified on a real PostgreSQL** (none installed). The 4 Postgres tests are skipped until `TEST_POSTGRES_URL` is set. See `docs/postgres-setup.md` |
| M11 | Authentication, RBAC, department permissions | Done, pushed. Security report: `docs/security-test-report.md` |
| M12 | Hybrid search and reranking | Done, pushed. Measured against M7: `docs/retrieval-baseline.md` section 6 |
| M13 | RAGAS evaluation | Built and tested offline (separate `.venv-eval`). **No real scores yet:** needs `OPENAI_API_KEY`; the live `ragas.evaluate` call has never run. See `docs/ragas-evaluation.md` |
| M14 | Docker, deployment, monitoring, hardening | Dockerfile, compose, `/health/ready`, security headers written. **Docker never built or run** (not installed). Not done: TLS, CI, metrics, backups, more than one API worker. See `docs/deployment.md` |

## Environment facts (checked 2026-10-31)

- Git repo on `main`, in sync with `origin` (`https://github.com/rishabhrathod12/Chatbot_Apex_Industries.git`).
- App environment: `.venv/`, Python 3.12.10. RAGAS environment: `.venv-eval/` (kept separate because ragas downgrades `openai`). The old `apex/` venv (Python 3.14.5) is unused.
- Docker and PostgreSQL are **not installed**. No `OPENAI_API_KEY` is set in `.env`.
- 6 sample PDFs in `data/sample_docs/` (fictional).
- Offline test suite: 865 passed, 4 skipped (Postgres), 2 xfailed, 1 deselected (live API). Ruff and mypy clean.

## Open decisions (need user approval)

See `docs/decisions/ADR-0001-initial-decisions.md`, section "Pending".

## Known issues and gaps

- M10, M13 and M14 have never run against the real thing (Postgres, a live judge model, Docker). Expect small first-run fixes.
- No TLS in the Docker setup: do not expose it to other people without a reverse proxy.
- One API process only: the embedded Qdrant store and the in-memory login limiter do not support several.
- Login rate limiting sees the web container's address when run in Docker, so it limits per email only.
- No CI pipeline, metrics or alerting, and no automated or tested backups.
- Keyword search scans chunks in Python (fine for thousands of chunks, not millions); see `docs/retrieval-baseline.md`.
- `frontend/package-lock.json` is untracked and looks like a stray file.

## Change log

- 2026-10-30: M0 documents drafted. M1 and M2 built.
- 2026-10-31: M3 to M9 built (upload, extraction, chunking, vector store, RAG, citations, Streamlit interface).
- 2026-10-31: M11 (login, roles, permissions), M12 (hybrid search, reranking), M13 (RAGAS, offline), M10 (PostgreSQL support, unverified) and M14 (Docker, readiness check, security headers, unverified). All pushed to GitHub.
- 2026-10-31: status file brought up to date (it still described the 2026-10-30 state with no code).
