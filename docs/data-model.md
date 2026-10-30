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

## 2. Chroma chunk metadata (set from M5 onward)

| Field | Type | Notes |
|---|---|---|
| `chunk_id` | str | Deterministic: `{document_id}:{doc_version}:{chunk_index}` |
| `document_id` | str | Internal UUID |
| `doc_code` | str | e.g. `APM-PROD-MAN-001`, from the PDF's metadata table |
| `doc_title` | str | |
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
- `document_chunks` (id, document_id, chunk_index, page_start, page_end, section_title, content_hash). Text lives in Chroma and the DB holds the registry used to reconcile.
- `conversations` (id, user_id, title, created_at)
- `messages` (id, conversation_id, role, content, citations_json, created_at)
- `audit_log` (id, user_id, action, target_type, target_id, outcome, created_at)

Unique constraint: (`scope`, `owner_id` or `department_id`, `file_hash`) to prevent duplicate ingestion.

## 4. Document status

`uploaded -> processing -> ready` or `failed`. Updates create a new version. The old version is removed only after the new one is `ready`.
