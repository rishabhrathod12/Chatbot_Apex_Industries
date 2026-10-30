# Data Model (draft)

## 1. Canonical departments

| Canonical | Aliases seen in PDFs / brief |
|---|---|
| production | Production |
| maintenance | Maintenance |
| hr | HR |
| safety | Safety |
| quality | Quality, Quality Control |
| stores | Stores (no sample document yet) |

## 2. Vector store payload (Qdrant; set from M5/M6 onward)

| Field | Type | Notes |
|---|---|---|
| `chunk_id` | str | Deterministic: `{document_id}:{doc_version}:{chunk_index}` |
| `document_id` | str | Internal UUID |
| `doc_code` | str | e.g. `APM-PROD-MAN-001`, from the PDF's metadata table |
| `doc_title` | str | |
| `source_name` | str | Original filename, used in citations |
| `kind` | str | `body` or `title_block` |
| `doc_version` | str | e.g. `1.0` |
| `scope` | str | `enterprise` or `personal` |
| `department` | str | Canonical value. Empty for personal |
| `owner_id` | str | Uploader. Required for personal scope |
| `page_start`, `page_end` | int | PDF only. Null for DOCX/TXT |
| `section_title` | str | Nearest heading |
| `chunk_index` | int | |
| `content_hash` | str | SHA-256 of chunk text |

Collections: `enterprise_chunks` and `personal_chunks` (see ADR-0001, decision D6).

## 3. PostgreSQL tables (draft)

- `users` (id, email, password_hash, role, is_active, created_at)
- `departments` (id, code, name)
- `user_departments` (user_id, department_id)
- `documents` (id, scope, owner_id, department_id, doc_code, title, version, file_hash, storage_path, original_filename, mime_type, size_bytes, page_count, status, status_reason, created_at, updated_at, deleted_at)
- `document_chunks` (id, document_id, chunk_index, page_start, page_end, section_title, content_hash). Text lives in the vector store and the DB holds the registry used to reconcile.
- `conversations` (id, user_id, title, created_at)
- `messages` (id, conversation_id, role, content, citations_json, created_at)
- `audit_log` (id, user_id, action, target_type, target_id, outcome, created_at)

Unique constraint: (`scope`, `owner_id` or `department_id`, `file_hash`) to prevent duplicate ingestion.

## 4. Document status

`uploaded -> processing -> ready` or `failed`. Updates create a new version. The old version is removed only after the new one is `ready`.

## 5. Extraction output (M4)

`extract(path, ext)` returns `ExtractedDocument(pages, metadata, warnings)`.

- `Page.number` is the real 1-based PDF page. It is `None` for DOCX/TXT (no stable pages).
- Each page holds typed `Block`s, each with `kind` and `section_title`. Section state carries across page breaks, so a section spanning two pages reports the right page for each part.

| Block kind | Meaning | Index it? (decided at M5) |
|---|---|---|
| `body` | Real content | Yes |
| `title_block` | Cover title + metadata table (keeps the Document ID searchable) | Proposed: yes |
| `boilerplate` | "IMPORTANT NOTICE" and "Document control" text, identical in all six samples | No |
| `revision_history` | Version table | No |

Failure reasons (`ExtractionError.reason`): `corrupt_pdf`, `encrypted_pdf`, `too_many_pages`, `page_too_large`, `no_text_layer`, `corrupt_docx`, `invalid_encoding`, `empty_document`, `unsupported_type`.

## 6. Vector store (M6)

Two embedded Qdrant collections: `enterprise_chunks` and `personal_chunks`. Cosine distance, vector size from the embedding model (1536 for `text-embedding-3-small`). Point id = UUIDv5 of the `chunk_id`. Payload = the metadata in section 2, plus `text` and `embedding_model`.

- A collection refuses to open with a different vector size than it was created with (`embedding_dimension_mismatch`). Changing the embedding model means re-indexing everything.
- `SearchFilter(departments, owner_id, document_ids)`: `None` = unrestricted, an empty list = **no results** (fails closed).
- Payload indexes are not created. Embedded mode ignores them. Create them if this moves to a Qdrant server.
- Document rows now also hold `status_reason`, `chunk_count`, `page_count`, `doc_code`, `title`, `doc_version` (migration 0002).

## 7. Conversations (M8)

- `conversations` (id, user_id, title, created_at, updated_at). Index on (user_id, updated_at).
- `messages` (id, conversation_id, position, role, content, status, reason, sources_json, rewritten_question, created_at). Index on (conversation_id, position). Deleting a conversation deletes its messages.
- `sources_json` is a snapshot of the sources shown with the answer, including the verbatim `quote`. If the source document is later changed or deleted, the saved answer keeps showing what it showed at the time.
- The user's own wording is stored as typed. The rewritten standalone question (follow-ups only) is stored on the assistant message.
- A conversation is capped at 200 messages.

## 8. Users and permissions (M11)

- `users` (id, email unique and lower-case, full_name, password_hash, role, is_active, created_at). Role is `super_admin`, `department_manager` or `employee`.
- `user_departments` (user_id, department). A user can belong to several departments. Unique per pair.
- `refresh_tokens` (id, user_id, token_hash unique, created_at, expires_at, revoked_at). Only the SHA-256 of the token is stored.
- `audit_log` (id, user_id, action, outcome `ok|denied|failed`, target_type, target_id, created_at). Ids and codes only.
- `documents.visibility`: `department` (default), `company` or `personal`. The same value is stored on every chunk in the search index (`visibility` payload field), which is what lets one search filter say "my departments, or anything company-wide".
- Search filter (`SearchFilter`): `departments` + `include_company_wide` (allowed set), `narrow_department` (shrinks only), `owner_id` (personal). An empty allowed set returns nothing.
