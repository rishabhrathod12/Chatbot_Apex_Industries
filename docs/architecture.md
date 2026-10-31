# Architecture

**Verified against:** the code on 2026-10-31 (database migration `0008`). Table and column details are in `data-model.md`, the security rules and their tests in `security.md`, running it in `deployment.md`.

## 1. Overview

```
Streamlit web interface  --HTTP/JSON, bearer token-->  FastAPI backend (one process)
 landing: Employee / Super Admin                        |- api/          routers, request checks
 employee workspace, admin workspace                    |- services/     business rules
                                                        |- repositories/ database access
                                                        |- rag/          ingestion, vectorstore, retrieval, generation
                                                        |
        SQLite (default) or PostgreSQL <----------------+----------------> Qdrant (embedded, vector_db/)
        users, documents, conversations,                                     chunk text + vectors + permission metadata
        tokens, audit log
        Uploaded files (storage/uploads/)                                     OpenAI API: embeddings, answers, reranking
```

TLS is not built in: put a reverse proxy in front before anyone else uses it (`deployment.md`).

**Principle: the backend decides what a person may see.** Neither the web page nor the language model makes an access decision. The web page only chooses what to *show*; the server checks every request again.

## 2. Components

| Component | Location |
|---|---|
| Web interface | `frontend/` (section 8) |
| API routers | `backend/app/api/` (`auth`, `users`, `admin`, `documents`, `chat`, `conversations`, `search`, `health`) |
| Authentication and permissions | `core/security.py` (passwords, tokens), `core/permissions.py` (every access decision and the search filter), `core/rate_limit.py`, `api/deps.py` (who is asking), `services/auth.py`, `services/setup.py` |
| Accounts and the admin dashboard | `services/users.py`, `services/admin.py`, `services/audit.py` |
| Document handling | `services/documents.py`, `services/file_validation.py`, `repositories/` |
| Ingestion pipeline | `rag/ingestion/pipeline.py` (also swaps in a new document version) |
| Text extraction and cleaning | `rag/ingestion/extractors/` (PDF, DOCX, TXT), `cleaners.py`, `metadata.py`, `segmenter.py` |
| Chunking | `rag/ingestion/chunkers/` (section-aware, page-tracked) |
| Embeddings | `rag/ingestion/embedder.py` (OpenAI, or an offline hashing embedder for tests) |
| Vector database | `rag/vectorstore/` (an interface plus the Qdrant implementation) |
| Retrieval | `rag/retrieval/` (`semantic`, `lexical` BM25, `hybrid` fusion) |
| Answer generation | `rag/generation/`: `orchestrator.py` (the steps), `rerank.py`, `condense.py` (follow-ups), `prompts.py`, `llm.py`, `citations.py` |
| Conversations | `services/chat.py`, `models/conversation.py` |
| Settings, database, logging | `core/config.py`, `core/database.py`, `core/logging.py` |
| Command line | `app/cli.py`: `create-user`, `seed-demo`, `reset-password`, `copy-db` |
| Evaluation | `eval/`: retrieval and answer checks (`run`, `compare`, `run_answers`), RAGAS scoring (`ragas_*`) |
| Database migrations | `backend/alembic/` |

## 3. Who is asking: identity and access

1. **Sign in.** The page sends a Login ID (or e-mail), the password and which portal was used (`employee` or `admin`). The server checks the password (scrypt), the lockout (5 wrong tries per person and address in 15 minutes), the active flag, and that the account's real role belongs to that portal. A mismatch gets the same answer as a wrong password.
2. **Tokens.** A short-lived access token (30 minutes) holds only the user id. A refresh token (8 hours) is stored only as a hash, is single-use, and ends all sessions if an old one is presented again.
3. **Every request** loads the person from the database: role, departments and `is_active` are never taken from the token, so a demotion or a disabled account applies on the next request.
4. **Temporary passwords.** An account created or reset by an admin has `must_change_password` set; until the person chooses their own password the server refuses everything except `/auth/me`, `/auth/change-password` and sign-out.
5. **Search filter.** `core/permissions.py: build_search_filter` turns the signed-in person into a `SearchFilter` (their departments plus company-wide, or just their own personal documents). Request fields such as `department` can only *narrow* it. The filter is applied inside the vector search and the keyword search, so nothing outside it is ever scored, ranked or sent to a language model.

`/search` returns raw passages instead of an answer. Any signed-in person can call it, within their own permissions. It is kept as a retrieval inspector, and the security tests lean on it heavily.

## 4. Ingestion flow

```
Upload
 -> checks: file name cleaned, type allowed for this scope (personal: PDF only), size cap,
    SHA-256 computed while streaming, duplicate? (same file in the same library -> "duplicate"),
    contents match the type (magic bytes, DOCX size limits)
 -> stored under a random name; database row, status "uploaded"; the upload request returns here
Background, one document at a time:
 -> status "processing" (skipped if the document was archived while waiting)
 -> extract page by page (max 200 pages), strip repeated headers/footers, read the metadata table
 -> chunk (about 600 tokens, 80 overlap, headings and page numbers kept)
 -> embed every chunk BEFORE touching the store, so an embedding failure leaves the store alone
 -> upsert into Qdrant (deterministic chunk ids) and drop stale chunks of that document
 -> status "ready"   (any failure: vectors removed again, status "failed" with a reason code)
```

A new version of a company document goes through the same pipeline; the old version is archived only after the new one is `ready`. Archiving removes a document's vectors immediately. See `data-model.md` section 4.

## 5. Question and answer flow

```
Question + bearer token
 -> the server builds the SearchFilter from the signed-in person (never from the request)
 -> follow-up? condense the last few turns into one standalone question (a model call)
 -> retrieve with the filter:
      meaning search (top 20) + keyword search over the permitted chunks (BM25, up to 5000)
      -> reciprocal-rank fusion -> best 8 (best 5 if reranking is off)
 -> gate 1: each passage must reach a meaning-similarity of 0.25 (RETRIEVAL_MIN_SCORE)
      none do  -> "not found" (no_relevant_context), no further model call
 -> gate 2 (reranking on): a model scores each passage 0 to 3 for how well it answers; keep 2 or more
      none left -> "not found" (low_relevance)
 -> the answering model sees ONLY the kept passages, numbered, inside delimiters, with rules:
      use only the excerpts, cite [n] after each statement, reply NOT_FOUND if they do not answer
 -> checks: the citations must point at excerpts that were really sent; an answer citing nothing is dropped
 -> sources are built from stored chunk metadata (never from model output), each with a verbatim quote
 -> the question and answer are saved to the person's conversation; logs hold ids and timings only
```

Refusal reasons: `no_relevant_context`, `low_relevance`, `model_declined`, `uncited_answer`, `empty_answer`. Consequences worth knowing:

- The system answers **specific questions** well. Requests about a whole document ("summarise this document", "what is my name?") often come back as not found, because no single passage answers them and the second gate throws related-but-not-answering passages away.
- Text that is not in the PDF's text layer (pictures, scanned pages, garbled font encodings) cannot be found. There is no OCR.

## 6. API surface

| Access | Routes |
|---|---|
| Public | `GET /health` (also reports `api_level`), `GET /health/ready`, `GET` and `POST /auth/setup` (first-time setup, closed once any user exists), `POST /auth/login`, `/auth/refresh`, `/auth/logout` |
| Allowed with a temporary password | `GET /auth/me`, `POST /auth/change-password` |
| Any signed-in person (resource checks inside) | `POST /chat`, `POST /search`; `GET /conversations`, `GET` and `DELETE /conversations/{id}`; `GET` and `POST /documents`, `GET` and `DELETE /documents/{id}`, `POST /documents/{id}/{reindex,archive,restore,versions}`, `GET /documents/{id}/versions` |
| Super Admin only | `GET /admin/stats`, `GET /admin/audit`, `GET` and `POST /users`, `PATCH /users/{id}`, `POST /users/{id}/password` |

Everything is under `/api/v1`. A test fails if a route is added without being classified into one of these rows. Responses carry an `X-Request-ID` and security headers; a document or conversation you may not read answers exactly like one that does not exist (404).

## 7. Deployment shapes

- **Development:** `scripts\dev.ps1` runs the API (port 8000) and the web page (port 8501) from `.venv`, with SQLite and the embedded vector store on disk. It refuses to start over an old copy still holding a port (`-Restart` replaces it).
- **Docker:** `docker-compose.yml` runs PostgreSQL, the API (one worker, because the embedded vector store allows one process) and the web page, ports on 127.0.0.1 only, secrets from `.env`, migrations applied at start. Health checks use `/health/ready`.
- **RAGAS scoring** runs from a separate environment (`.venv-eval`) because ragas needs an older `openai` package.

## 8. Web interface

`frontend/streamlit_app.py` is a thin router: it checks that the API is up and recent enough (`GET /health` must report `api_level` 2 or more), shows the sign-in pages to anyone signed out, the forced password change if needed, and otherwise a sidebar workspace chosen by the **role the server reports**.

| Module | Holds |
|---|---|
| `auth_views.py` | landing page with the two portals, sign-in, first-time setup, forced password change |
| `employee_views.py` | the employee dashboard |
| `chat_views.py` | asking questions, rendering answers and sources, conversation history |
| `document_views.py` | personal uploads and list, the company library, company upload, archive, versions |
| `admin_views.py` | overview, employee management, create employee, departments, audit log |
| `api_client.py` | the only code that talks to the API; keeps the tokens and renews them once on a 401 |
| `ui.py` | plain display helpers and the page lists per role (no Streamlit calls, easy to test) |

Employees see: Dashboard, Upload PDF, My Documents, Chat with My Documents, Company Knowledge, Conversation History (managers add Department Documents). Super Admins see: Overview, Employee Management, Create Employee, Company Documents, Upload Company Documents, Departments, Audit Logs, Ask Company Documents. The client object is kept in the browser session and replaced if the code behind it has changed.

## 9. Evaluation

`eval/` builds a throwaway index of the sample documents and measures: retrieval (`python -m eval.run`), answers and refusals (`eval.run_answers`), settings comparisons (`eval.compare`), and RAGAS faithfulness, answer relevancy and context precision (`eval.ragas_run`, from `.venv-eval`). Results and the current baselines: `retrieval-baseline.md`, `ragas-evaluation.md`.

## 10. Background: the sample dataset (drove several design choices)

- All six PDFs are 3 pages, text-based, no OCR needed.
- Page 1 has a metadata table (Document ID, Department, Version, Effective date, Owner, Classification, Review status, Approval), parsed at ingestion to fill the metadata.
- Page 1 is mostly title block and a disclaimer that is identical in all six documents and would match almost every query, so it is excluded from the index (`boilerplate`).
- Every page repeats a header with its own page number; it is stripped, and the real page index is kept.
- In documents 02, 03, 05 and 06 the "Revision history" heading sits alone at the bottom of page 2 and its table is on page 3; revision tables are not indexed.
- The PDFs say "Quality"; the company list says "Quality Control". One canonical list with aliases handles it.
- **Stores has no document**, although the Production SOP mentions Stores.
- Document ids follow `APM-<DEPT>-<TYPE>-<NNN>`, which makes good exact-match test queries for keyword search.

## 11. Decisions, limits and what comes next

Decisions: `docs/decisions/ADR-0001-initial-decisions.md`.

Current limits (also in `PROJECT_STATUS.md`): one API process (embedded vector store, in-memory login limiter and last-admin lock); no TLS or CI; keyword search scans permitted chunks in Python; no OCR; PostgreSQL and Docker are written but have not been run for real.

Scaling path: Qdrant server or pgvector behind the `VectorStore` interface, a task queue for ingestion, several API workers with a shared limiter, S3-compatible file storage, SSO, caching of embeddings and answers, OCR for image pages.
