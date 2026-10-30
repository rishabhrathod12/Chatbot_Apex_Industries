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

## ADR-0007: How answers are grounded and checked (M7, 2026-10-31)

Hallucination is reduced in layers, because no single one is reliable (see `docs/retrieval-baseline.md`):

1. Only chunks the permission filter allows AND that score at least `RETRIEVAL_MIN_SCORE` reach the model. If none do, the model is not called.
2. The prompt confines the model to numbered excerpts, tells it to cite `[n]` after each statement and to reply `NOT_FOUND` if the excerpts don't answer.
3. Excerpts are wrapped in `<context>` tags and any such tag inside document text, labels or the question is neutralised, so text can't break out of the block. The prompt says excerpts are data, not instructions.
4. The answer is checked in code: markers pointing at excerpts we never sent are stripped, and an answer with **no** valid citation is discarded and replaced by "not found" (`uncited_answer`). Sources are built from stored chunk metadata, never from model text.

Trade-off: strict rule 4 will occasionally discard a correct but uncited answer. For a company-policy assistant a false refusal is preferable to an unverifiable answer. Revisit with measured data.

Measured outcome (2026-10-31, see `docs/retrieval-baseline.md`):
- `CHAT_MODEL=gpt-4.1-mini` works on the account. `RETRIEVAL_MIN_SCORE` is now 0.40 (was a 0.25 placeholder), set from the measured score ranges.
- 20/20 answerable questions answered with the right source, 0 false refusals.
- Weakness: on a near-miss question (D06) the model sometimes answers a neighbouring question with a true, correctly cited sentence (2 of 9 runs). The citation check cannot catch this. A stricter prompt rule made it worse (5 of 5) and was reverted. Mitigation is measurement (RAGAS answer relevancy, M13) and a larger near-miss set, not more prompt tuning.
- Golden-set types: `unanswerable` (refusal required), `not_specified` (refusal OR grounded "not specified" accepted, answers flagged for manual review).

## ADR-0008: Conversation memory and source quotes (M8, 2026-10-31)

1. **Follow-ups are rewritten, not replayed.** One small model call turns the new message into a standalone question using the last 3 turns (`CHAT_HISTORY_TURNS`). Retrieval and the answering prompt then see only that question, never the history. This keeps earlier text from steering the answer and keeps the answering prompt identical to the one measured at M7. If the rewrite fails or looks wrong, the original message is used.
2. **Quotes are selected by code, not by the model.** The `quote` on each source is the chunk line that shares the most words with the part of the answer that cites it. It is always copied from the chunk, so it can be checked. A test checks that every quote for every golden question appears in the text of the cited page.
3. **Ownership.** A conversation belongs to one user. Anyone else gets 404, identical to the response for a conversation that does not exist. `get_current_user_id` is the single place the user comes from; M11 replaces it with JWT.
4. **Nothing is saved if answering fails**, so a model outage never leaves a half-written conversation.
5. **Retention:** conversations are kept until the user deletes them. Revisit at M14.

Limitations: a saved answer is a snapshot and stays visible to its owner even if their access or the source document changes later. The rewrite step costs one extra model call per follow-up.

## ADR-0009: Web interface design (M9 first version, 2026-10-31)

1. **Thin client.** The Streamlit app holds no secrets and no business rules. It calls the API over HTTP (`frontend/api_client.py`) and shows what comes back. Streamlit runs server-side, so the browser never talks to the API directly and no CORS setup is needed.
2. **Model output is treated as untrusted.** Answers are rendered as markdown, and a markdown image is fetched automatically by the browser, which could leak text through the image address if a document contained an injected instruction. So images are removed and links lose their address before display (`ui.safe_markdown`). Quotes copied from documents, file names and the user's own text are escaped so they show literally (`ui.escape_markdown`). Raw HTML is never enabled. Both helpers are tested.
3. **Single source of truth.** After each question the page reloads the conversation from the API instead of keeping its own copy, so it cannot drift from what is stored.
4. **The document list refreshes itself every 3 seconds** (a Streamlit fragment) so status changes from Processing to Ready without clicking. Checked in a real browser: it does not interfere with the delete confirmation.
5. **Department and scope pickers are demo controls.** They stand in for permissions until M11; the sidebar says so.
6. **Deep links:** `?page=documents` and `?c=<id>` (the id must be 32 hex characters, anything else is ignored).
7. Upload size shown in the uploader matches the API limit (`maxUploadSize = 20` in `.streamlit/config.toml`).

Verified in a real browser (Edge, driven by Playwright) against the live API with real OpenAI models: welcome screen, answer with sources and quote, refusal, document library, clicking an example question, typing a follow-up, upload of a text file, rejection of a fake PDF with a readable reason, and delete with confirmation. Playwright was used in a throwaway environment and is not a project dependency, so these browser checks are not part of the automated test suite. The suite covers the page logic with Streamlit's own test runner and a fake API.

## ADR-0010: Authentication and permissions (M11, 2026-10-31)

Decided by the user: the Safety manual and HR policy are company-wide, everything else is department-only; Department Managers may publish company-wide for their own department.

1. **Passwords: scrypt from the standard library**, not argon2 or bcrypt. Both are fine; scrypt avoids another native package, and this machine has already crashed on native wheels (ChromaDB, PyMuPDF 2.0). Cost (N=2^15) is stored in each hash so it can be raised later. Tests use a lower cost. Revisit argon2 if there is a reason to.
2. **Access tokens: short-lived HS256 JWTs (30 min) that carry only the user id.** Role and departments are looked up on every request, so changes and account disabling apply at once. This costs one small database read per request and is worth it.
3. **Refresh tokens: random, single-use, rotated, stored hashed.** Reusing a spent one ends all of that user's sessions.
4. **404 for what you cannot see, 403 for what you can see but not change.** Ids cannot be probed.
5. **Personal data is private from everyone, including Super Admins.** Admins manage the company library, not people's private files or chats.
6. **One module decides access** (`core/permissions.py`); the search filter is built from the logged-in user on the server and applied inside the vector search. The department box in the UI can only narrow.
7. **No default account.** The first Super Admin is created on the command line. `seed-demo` is development only.
8. **Failed-login auditing stores a fingerprint, not the email.**
9. **The login limiter is in memory** (single process). Redis or similar at M14.
10. **Existing M3-M10 data:** documents and conversations created before login belonged to a placeholder user and are not visible to anyone after upgrading. For development, wipe `storage/` and `vector_db/` and re-upload (the `.gitignore`d demo data). A real deployment would migrate owners first.

Bug found by the tests while building this: changing a user's departments to an overlapping set crashed with a 500 (the old rows were cleared and re-added, violating the unique constraint). Fixed by writing only the difference.
