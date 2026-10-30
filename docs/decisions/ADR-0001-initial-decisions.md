# ADR-0001: Initial decisions

Status: **Approved D1-D9 and ADR-0002 reordering** (user replied "yes", 2026-10-30). The "Pending" list below is still open unless marked otherwise. Date: 2026-10-30.

## Recommended decisions

| ID | Decision | Reason |
|---|---|---|
| D1 | Python 3.12, venv at `.venv/` | Most stable for ChromaDB, PyMuPDF and the AI libraries. Existing venv is 3.14.5 |
| D2 | LangChain used narrowly (splitters, OpenAI wrappers). Orchestration written by us | Fast-moving library, hides what happens. We want to explain every step |
| D3 | Chroma behind a `VectorStore` interface | Chroma is not a security boundary and has scaling limits. Keeps pgvector/Qdrant open |
| D4 | Citations built from chunk metadata, not model text | Model cannot invent a page number |
| D5 | No Celery/Redis. FastAPI `BackgroundTasks` for ingestion | Documents are small. Add a queue only when needed |
| D6 | Two Chroma collections: `enterprise_chunks`, `personal_chunks`. Department filtering by metadata inside enterprise | Personal and company data have different owners and lifecycles |
| D7 | Fake embedder and fake LLM in normal tests. Live API tests are opt-in | Free, offline, deterministic test suite |
| D8 | PDF citations use page numbers. DOCX/TXT citations use section headings | DOCX/TXT have no real pages |
| D9 | Strip repeated header/footer, drop or tag disclaimer and revision-history blocks | See architecture.md section 5 |

## ADR-0002 (proposed): Milestone order changes

1. SQLAlchemy and Alembic move to M3 (uploads need DB records). SQLite for quick tests, Postgres later.
2. Permission metadata (`scope`, `department`, `owner_id`) is in chunk metadata from M5, enforcement arrives at M11.
3. A small golden set and retrieval metrics are built at M7. RAGAS stays at M13.

## Pending (need user decision)

1. Approve D1-D9 and the milestone reordering.
2. Reranker: local cross-encoder (PyTorch, heavy) vs LLM-based (costlier, lighter install). Decide before M12.
3. Fate of existing `apex/` venv: delete, or keep and git-ignore.
4. Move PDFs into `data/sample_docs/` at M1?
5. Extra dependencies: `pydantic-settings`, `rank_bm25`, `python-docx`, password hasher, `pre-commit`, gitleaks.
6. Are Safety and HR documents company-wide or department-only? (affects the RBAC matrix).
7. Docker Desktop now or at M14? Postgres natively or in Docker?
8. Default OpenAI chat and embedding models, and an evaluation budget.
9. Seeded demo users per role/department, or admin-created only?
10. Documents English-only?
11. Local-only demo or hosted demo?

## ADR-0003: PyMuPDF pinned below 2.0 (found at M4, 2026-10-31)

`pymupdf` 2.0 crashed the interpreter (segmentation fault on `import pymupdf`) on Windows with Python 3.12.10. Version 1.28.2 works. The dependency is pinned as `pymupdf>=1.24,<2`. Revisit when 2.x is confirmed stable on this platform.

## ADR-0004: Own section-aware splitter instead of LangChain's (M5, 2026-10-31)

D2 said to use LangChain's text splitters. A generic splitter works on one flat string and loses which page each part came from, and page numbers are a core requirement (citations). `SectionChunker` packs paragraph/bullet units that each carry their page, so a chunk's `page_start`/`page_end` are exact. It is about 150 lines, deterministic, and fully tested. LangChain stays in use only where it helps (OpenAI wrappers at M6/M7). Chunk size is estimated at 4 characters per token. Swap in a real tokenizer if sizing ever matters.

## ADR-0005: Qdrant (embedded) replaces ChromaDB (M6, 2026-10-31)

ChromaDB crashed the Python process (access violation on the first `upsert`) on the dev machine in every version tried that installs from a wheel (1.0.21, 1.1.1, 1.3.7, 1.5.9), in memory and on disk, at any vector size. The 0.x line needs a C++ compiler to build `chroma-hnswlib`, which isn't installed. Qdrant's embedded mode (`qdrant-client`, pure Python) passed the same smoke test: 1536-dim vectors, filters, delete-by-filter, persistence. The user approved the switch.

This is the swap D3 was designed for: only `rag/vectorstore/qdrant_store.py` changed. Two collections (D6) are unchanged.

Consequences: embedded Qdrant allows one process per storage folder, so the API runs as a single process for now (several workers would need a Qdrant server). Payload indexes are skipped in embedded mode.

## ADR-0006: Offline hashing embedder (M6, 2026-10-31)

`HashingEmbedder` (word hashing, no semantics) lets the full pipeline run and be tested without an API key or network. It is blocked in production by a settings check. Its retrieval results are a plumbing baseline only. On the 16 golden questions it puts the right document in the top 3 for 14. The other 2 are marked `xfail(strict)`. Real retrieval quality is measured with the OpenAI embedder at M7.
