# Project Status

**Project:** Manufacturing AI Knowledge Assistant, Apex Precision Manufacturing Pvt. Ltd.
**Last updated:** 2026-10-30
**Current milestone:** M4 complete locally. Next: M5 (awaiting approval)

## Milestone tracker

| # | Milestone | Status |
|---|---|---|
| M0 | Architecture and requirements | Approved 2026-10-30 (pending questions remain, none block M1 given defaults) |
| M1 | Project foundation and repo setup | Done locally 2026-10-30 (commit e7e6d33, not pushed) |
| M2 | Backend foundation and configuration | Done locally 2026-10-30 (committed, not pushed) |
| M3 | Document upload and storage (+ SQLAlchemy/Alembic) | Done locally 2026-10-31 (committed, not pushed) |
| M4 | PDF/DOCX/TXT extraction and preprocessing | Done locally 2026-10-31 (committed, not pushed) |
| M5 | Chunking and metadata management | Not started |
| M6 | Embeddings and ChromaDB integration | Not started |
| M7 | Basic RAG + first golden set | Not started |
| M8 | Citations and conversational memory | Not started |
| M9 | Streamlit frontend | Not started |
| M10 | PostgreSQL and enterprise document management | Not started |
| M11 | Authentication, RBAC, department permissions | Not started |
| M12 | Hybrid search and reranking | Not started |
| M13 | RAGAS evaluation | Not started |
| M14 | Docker, deployment, monitoring, hardening | Not started |

## Environment facts (checked 2026-10-30)

- Folder is not yet a git repo. Remote: `https://github.com/rishabhrathod12/Chatbot_Apex_Industries.git`
- Existing venv `apex/` is Python 3.14.5 (plan says 3.12). It is empty.
- Docker is not installed.
- 6 sample PDFs at repo root (3 pages each).

## Open decisions (need user approval)

See `docs/decisions/ADR-0001-initial-decisions.md`, section "Pending".

## Known issues

None yet. No code exists.

## Change log

- 2026-10-30: M0 documents drafted (architecture, security, data model, ADRs, golden questions). No code written.
