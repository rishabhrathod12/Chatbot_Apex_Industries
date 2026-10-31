# Data Model

**Verified against:** database migration `0008` and the code on 2026-10-31. A test (`test_migrations_produce_exactly_the_schema_the_models_describe`) keeps the migrations and the models in step, but nothing keeps *this page* in step, so check it against `backend/app/models/` when you change a table.

Data lives in three places:

| What | Where | Notes |
|---|---|---|
| Records (users, documents, conversations, audit) | A relational database: SQLite by default (`storage/app.db`), PostgreSQL through `DATABASE_URL` | Schema changes are Alembic migrations in `backend/alembic/versions/` |
| The searchable text and its vectors | Embedded Qdrant (`vector_db/`) | Two collections, see section 6 |
| The uploaded files themselves | `storage/uploads/`, under random file names | Never served directly; a document is only reachable through the API's permission checks |

## 1. Departments

Fixed in code (`backend/app/core/departments.py`), not a table. The web page keeps its own copy of the list (`frontend/ui.py`) because it only talks to the API.

| Canonical code | Aliases accepted |
|---|---|
| `production` | Production |
| `maintenance` | Maintenance |
| `hr` | HR |
| `safety` | Safety |
| `quality` | Quality, Quality Control, QC |
| `stores` | Stores (no sample document) |

## 2. Relational tables

```
users 1---* user_departments          users 1---* refresh_tokens
conversations 1---* messages          documents 0..1---1 documents  (supersedes_id: a new version points at the one it replaces)
audit_log   (user_id, target_id are plain ids, not foreign keys, so the history survives)
documents.owner_id, conversations.user_id: plain ids as well (no foreign key)
```

### `users`
| Column | Type | Notes |
|---|---|---|
| `id` | str(32) PK | random hex |
| `email` | str(254) | unique, stored lower-case |
| `login_id` | str(32) | unique (index `ix_users_login_id`), lower-case; what people type to sign in, the email works too |
| `full_name` | str(120) | |
| `password_hash` | str(255) | scrypt, cost stored inside the hash; never returned by the API |
| `role` | str(24) | `super_admin`, `department_manager` or `employee` |
| `is_active` | bool | a disabled account is refused on the next request |
| `must_change_password` | bool, default false | true after an admin creates or resets the account |
| `password_changed_at` | datetime, null | |
| `created_by` | str(32), null | the admin who created the account; null for the command line and first-time setup |
| `created_at` | datetime | |

### `user_departments`
`id`, `user_id` (FK users), `department` (canonical code). Unique per (`user_id`, `department`). A person can belong to several departments; a Super Admin normally has none.

### `refresh_tokens`
`id`, `user_id` (FK users), `token_hash` (unique; only the SHA-256 of the token is stored), `created_at`, `expires_at`, `revoked_at` (null while live). Index on `user_id`. Tokens are single-use: refreshing revokes the old one, and presenting a revoked one ends all of that person's sessions.

### `audit_log`
| Column | Notes |
|---|---|
| `id` | auto-increment |
| `user_id` | who did it (null when nobody was signed in, such as a failed sign-in or a command-line action) |
| `action` | short code, see section 9 |
| `outcome` | `ok`, `denied` or `failed` |
| `target_type`, `target_id` | what it was done to (`user`, `document`, `department`, `portal`, `email` as a fingerprint, ...) |
| `detail` | optional short code such as `role:super_admin`; never free text |
| `created_at` | |

Index (`user_id`, `created_at`). It holds ids and codes only: never document text, questions, answers, passwords or tokens.

### `documents`
| Column | Type | Notes |
|---|---|---|
| `id` | str(32) PK | |
| `scope` | str(16) | `personal` or `enterprise` (company library) |
| `owner_id` | str(64) | the uploader. For personal documents this is the only person who can ever read it |
| `department` | str(32) | canonical code; empty for personal documents |
| `visibility` | str(16), default `department` | `personal`, `department` or `company` (readable by every signed-in person) |
| `dedupe_key` | str(128) | `enterprise:{department}` or `personal:{owner_id}` |
| `original_filename`, `stored_filename`, `file_ext`, `mime_type`, `size_bytes` | | the stored name is random; the original is shown in citations |
| `file_hash` | str(64) | SHA-256 of the file |
| `status` | str(16) | see section 4 |
| `status_reason`, `chunk_count`, `page_count` | | filled by ingestion |
| `doc_code`, `title`, `doc_version` | | **read from the file** by ingestion (the metadata table on page 1 of the sample PDFs) |
| `title_override`, `version_override` | str, null | **typed by an admin** at upload. They win over the file's own values and are never overwritten by re-indexing. They are what citations show. |
| `description`, `document_type` | | typed by an admin; `document_type` is one of `sop`, `manual`, `policy`, `form`, `checklist`, `report`, `other` |
| `archived_at` | datetime, null | set while archived |
| `supersedes_id` | str(32), null | the earlier version this document replaces (index `ix_documents_supersedes`) |
| `created_at`, `deleted_at` | | `deleted_at` is set by a delete (soft delete) |

Unique index `uq_documents_dedupe` on (`dedupe_key`, `file_hash`), **only while `deleted_at` is null**: one live copy of a file per department (or per person), and a deleted document does not block uploading it again. There is no separate chunk table: chunk text and metadata live only in the vector store (section 6).

### `conversations` and `messages`
- `conversations` (`id`, `user_id`, `title`, `created_at`, `updated_at`). Index (`user_id`, `updated_at`). Only the owner can read, continue or delete one, not even a Super Admin.
- `messages` (`id`, `conversation_id` FK, `position`, `role` `user|assistant`, `content`, `status` `answered|not_found`, `reason`, `sources_json`, `rewritten_question`, `created_at`). Index (`conversation_id`, `position`). Deleting a conversation deletes its messages. A conversation is capped at 200 messages.
- `sources_json` is a snapshot of the sources shown with the answer, including the verbatim `quote`. **When a conversation is opened, a source whose document the person can no longer read** (archived, deleted, moved out of their department) **is replaced by a blank placeholder** (`available: false`, number kept). The stored answer text itself is not rewritten.
- The person's own wording is stored as typed. The rewritten standalone question (follow-ups only) is stored on the assistant message.
- `reason` for a `not_found` answer: `no_relevant_context` (nothing similar enough), `low_relevance` (similar passages, but none answered the question), `model_declined`, `uncited_answer`, `empty_answer`.

## 3. Migrations

| Revision | Adds |
|---|---|
| 0001 | `documents` |
| 0002 | ingestion fields on `documents` (status reason, counts, code, title, version) |
| 0003 | `conversations`, `messages` |
| 0004 | `users`, `user_departments`, `refresh_tokens`, `audit_log`, `documents.visibility` |
| 0005 | `users.login_id` (backfilled from the e-mail, made unique), `must_change_password`, `password_changed_at`, `created_by` |
| 0006 | `audit_log.detail` |
| 0007 | `documents.description`, `document_type`, `title_override`, `version_override`, `archived_at` |
| 0008 | `documents.supersedes_id` |

`python -m app.cli copy-db --from sqlite:///./storage/app.db` copies a SQLite database into an empty, migrated PostgreSQL one (see `postgres-setup.md`).

## 4. Document lifecycle

```
upload -> uploaded -> processing -> ready
                          \-> failed        (re-index to retry)
ready -> archived          vectors removed first, so it cannot be searched or cited by anyone
archived -> uploaded -> processing -> ready     "restore": indexed again
delete -> deleted_at set, status "deleted", file and vectors removed
```

- **Archived** documents are visible only to the people who can manage them (a Super Admin, or the manager of that department). For everyone else they look as if they do not exist (HTTP 404) and are left out of counts. Personal documents are deleted, not archived.
- **New version** (`POST /documents/{id}/versions`): the new file is a new row whose `supersedes_id` is the old one. The old version stays searchable until the new one is `ready`, then it is archived. If the new one fails to index, the old one stays as it was.
- **Visibility** values and who can do what are in `security.md`. The same `visibility` and `department` are stored on every chunk in the vector store.

## 5. Extraction output

`extract(path, ext)` returns `ExtractedDocument(pages, metadata, warnings)`.

- `Page.number` is the real 1-based PDF page, and `None` for DOCX/TXT (no stable pages).
- Each page holds typed `Block`s, each with a `kind` and a `section_title`. Section state carries across page breaks.

| Block kind | Meaning | Indexed |
|---|---|---|
| `body` | Real content | yes |
| `title_block` | Cover title and metadata table (keeps the document id searchable) | yes |
| `boilerplate` | "IMPORTANT NOTICE" and "Document control" text, identical in all six samples | no |
| `revision_history` | Version table | no |

Extraction reads the **text layer only**; there is no OCR. A PDF with no text at all fails as `no_text_layer`; a PDF with some text but image-only pages (such as a picture cover) is indexed from the text it has and silently skips the images.

Failure reasons (`status_reason`): `corrupt_pdf`, `encrypted_pdf`, `too_many_pages`, `page_too_large`, `no_text_layer`, `corrupt_docx`, `invalid_encoding`, `empty_document`, `no_indexable_content`, `unsupported_type`, `embedding_not_configured`, `embedding_request_failed`, `internal_error`.

## 6. Vector store (embedded Qdrant)

Two collections, `enterprise_chunks` and `personal_chunks` (decision D6 in the ADR), so a company search can never touch a personal chunk. Cosine distance; vector size comes from the embedding model (1536 for `text-embedding-3-small`). Point id = UUIDv5 of the `chunk_id`.

| Payload field | Type | Notes |
|---|---|---|
| `chunk_id` | str | deterministic: `{document_id}:{doc_version}:{chunk_index}` |
| `document_id` | str | |
| `chunk_index` | int | |
| `doc_code` | str | e.g. `APM-PROD-MAN-001`, from the file's metadata table; empty if there is none |
| `doc_title` | str | the admin's `title_override` if there is one, otherwise the file's title |
| `doc_version` | str | the admin's `version_override`, else the file's, else `1` |
| `source_name` | str | original filename, used in citations |
| `section_title` | str | nearest heading |
| `kind` | str | `body` or `title_block` |
| `scope` | str | `enterprise` or `personal` |
| `department` | str | canonical; empty for personal |
| `visibility` | str | `personal`, `department` or `company` |
| `owner_id` | str | uploader; the key for personal chunks |
| `page_start`, `page_end` | int | PDF only; left out for DOCX/TXT |
| `content_hash` | str | SHA-256 of the chunk text |
| `embedding_model` | str | which model made the vector |
| `text` | str | the chunk itself |

- **Search filter** (`SearchFilter`): `departments` plus `include_company_wide` (the allowed set), `narrow_department` (can only shrink the result), `owner_id` (personal), `document_ids`. `None` means unrestricted, and an **empty list means no results** (fails closed). The filter is always built by the server from the signed-in person (`core/permissions.py: build_search_filter`), never from the request.
- A collection refuses to open with a different vector size than it was created with (`embedding_dimension_mismatch`). Changing the embedding model means re-indexing everything.
- Payload indexes are not created (embedded mode ignores them). Create them if this moves to a Qdrant server.
- Keyword search (BM25) works on the stored `text` of the chunks the filter allows (at most `LEXICAL_MAX_CHUNKS`, default 5000), and its word statistics are computed only from those chunks.

## 7. Users and permissions

- **Roles:** `super_admin` (everything company-wide, plus users and the audit log; reads nobody's personal documents or conversations), `department_manager` (their departments' documents), `employee` (reads their departments' and company-wide documents, adds personal ones).
- **Identity:** a token holds only the user id. Role, departments and `is_active` are read from the database on every request, so a change takes effect on the next request.
- **Sign-in portals:** the page sends `portal` (`employee` or `admin`) with the login. The server compares it with the stored role and refuses a mismatch with the same answer as a wrong password. It can only refuse, never grant.
- **Forced change:** while `must_change_password` is true, every route except `/auth/me`, `/auth/change-password` and sign-out answers `403 password_change_required`.

## 8. Settings that shape the data

`MAX_UPLOAD_MB` (20), `MAX_PDF_PAGES` (200), `PERSONAL_UPLOAD_EXTENSIONS` (default `.pdf`), `CHUNK_MAX_TOKENS` (600), `CHUNK_OVERLAP_TOKENS` (80), `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `ACCESS_TOKEN_MINUTES` (30), `REFRESH_TOKEN_HOURS` (8). The full list is in `backend/app/core/config.py` and `.env.example`.

## 9. Audit actions

`login`, `logout`, `refresh`, `refresh_token_reuse`, `setup_admin`, `user_create`, `user_update`, `user_activate`, `user_deactivate`, `user_role_change`, `user_departments_change`, `password_reset`, `password_change`, `admin_endpoint` (refused), `document_upload`, `document_delete`, `document_reindex`, `document_archive`, `document_restore`, `document_access` (refused), `document_version_upload`, `document_version_replace`. A request that changes nothing records nothing.
