# Architecture

## 1. Overview

```
Streamlit frontend  --HTTPS/JSON, JWT-->  FastAPI backend
                                           |- api/ (routers)
                                           |- services/ (business logic)
                                           |- repositories/ (DB access)
                                           |- rag/ingestion, vectorstore, retrieval, generation
                                           |
              PostgreSQL <-----------------+-----------------> ChromaDB
              (users, documents, chunks registry,              (vectors + metadata)
               conversations, audit)
              File storage (private volume)                    OpenAI API (embeddings, LLM)
```

Principle: **the backend decides what a user may see.** The frontend and the LLM never make access decisions.

## 2. Components

| # | Component | Location |
|---|---|---|
| 1 | Frontend | `frontend/` |
| 2 | Backend API | `backend/app/api/` |
| 3 | AuthN/AuthZ | `core/security.py`, `api/deps.py`, `services/auth` |
| 4 | Document management | `services/documents`, `repositories/` |
| 5 | Ingestion pipeline | `rag/ingestion/pipeline.py` |
| 6 | Text extraction | `rag/ingestion/extractors/` |
| 7 | Chunking | `rag/ingestion/chunkers/` |
| 8 | Embedding generation | `rag/ingestion/embedder.py` |
| 9 | Vector database | `rag/vectorstore/` (interface + Chroma impl) |
| 10 | Retrieval engine | `rag/retrieval/` |
| 11 | RAG orchestration | `rag/generation/orchestrator.py` |
| 12 | LLM integration | `rag/generation/llm.py` |
| 13 | Citation generation | `rag/generation/citations.py` |
| 14 | Conversation memory | `services/chat`, `models/conversation.py` |
| 15 | Evaluation | `eval/` |
| 16 | Logging/monitoring | `core/logging.py` |

## 3. Ingestion flow

```
Upload -> validate (extension, MIME, magic bytes, size)
 -> SHA-256 hash -> duplicate? -> reject / link existing
 -> store original (private, random filename)
 -> DB record (status=processing)
 -> extract per page -> clean (strip repeated header/footer)
 -> parse metadata table (Document ID, Department, Version, Effective date)
 -> chunk (section-aware, page-tracked)
 -> embed (batched) -> upsert to Chroma (deterministic chunk IDs)
 -> status=ready   (any failure -> status=failed, nothing half-indexed)
```

## 4. Retrieval and answer flow

```
Question + JWT
 -> server builds AccessFilter from token claims (never from the request body)
 -> (follow-up) condense to standalone query
 -> vector search WITH filter   \
 -> BM25 search WITH same filter  > RRF fusion -> rerank (M12+)
 -> re-check chunk IDs against Postgres (defence in depth)
 -> relevance gate: weak evidence -> "not found in your authorized documents"
 -> prompt (rules + numbered context + history + question) -> LLM
 -> citations built from chunk metadata (document, page, section)
 -> response + citations; log IDs and metrics only
```

## 5. Observations about the sample dataset (drives design)

- All six PDFs are 3 pages, text-based, no OCR needed.
- Page 1 has a metadata table: Document ID, Department, Version, Effective date, Owner, Classification, Review status, Approval. Parse it at ingestion to fill metadata automatically.
- Page 1 is mostly title block and disclaimer. Disclaimer text is identical in all six documents and would match almost every query, so it must be stripped or down-weighted at chunking.
- Every page repeats the header "Apex Precision Manufacturing Pvt. Ltd. | SAMPLE DOCUMENT - NOT FOR OPERATIONAL USE Page N". Strip it, and do not let the page number in it override the real page index.
- In documents 02, 03, 05 and 06 the "Revision history" heading sits alone at the bottom of page 2 and its table is on page 3. The revision table is low-value and should be dropped or tagged `section=revision_history`.
- Department values in the PDFs: Production, Maintenance, Safety, HR, Quality. The company list says "Quality Control". We need a canonical department list with aliases (see `data-model.md`).
- **Stores has no document.** Production SOP mentions Stores, so cross-department mentions exist without Stores owning content.
- Document IDs follow `APM-<DEPT>-<TYPE>-<NNN>` and make good exact-match test queries for hybrid search.

## 6. Main architectural decisions

See `docs/decisions/ADR-0001-initial-decisions.md`.

## 7. Scaling path (future, not now)

pgvector or Qdrant behind the `VectorStore` interface, task queue (Celery/RQ) for ingestion, multiple API workers, S3-compatible storage, SSO/OIDC, embedding and answer caching.
