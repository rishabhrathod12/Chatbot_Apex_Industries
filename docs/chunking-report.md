# Chunking Report (M5, 2026-10-31)

Strategy: `SectionChunker`, 600 max tokens, 80 overlap tokens (about 4 characters per token). Indexed block kinds: `body` and `title_block`. Excluded: `boilerplate`, `revision_history`.

## Results on the six sample PDFs

| Document | Code | Chunks | Min tokens | Mean tokens | Max tokens |
|---|---|---|---|---|---|
| 01_Machine_Operation_Manual.pdf | APM-PROD-MAN-001 | 8 | 33 | 90 | 132 |
| 02_Maintenance_Manual.pdf | APM-MNT-MAN-002 | 8 | 33 | 91 | 158 |
| 03_Factory_Safety_Manual.pdf | APM-HSE-MAN-003 | 9 | 27 | 71 | 105 |
| 04_Production_SOP.pdf | APM-PROD-SOP-004 | 10 | 31 | 67 | 95 |
| 05_HR_Policy.pdf | APM-HR-POL-005 | 9 | 27 | 69 | 98 |
| 06_Quality_Control_SOP.pdf | APM-QLT-SOP-006 | 9 | 22 | 72 | 95 |

Total chunks: 53; overall mean 76 tokens, min 22, max 158.

## What this shows

- Each document gives one cover chunk (keeps the Document ID searchable), one "Purpose" chunk, and one chunk per numbered section. All are cited to the right page: cover and Purpose to page 1, numbered sections to page 2.
- Disclaimer ("IMPORTANT NOTICE", "Document control") and revision tables are not in any chunk.
- **The size limit and the overlap never trigger on this data.** Every section is far below 600 tokens. Splitting, overlap and cross-page packing are covered by synthetic unit tests, not by the real documents.
- Chunks are small (mean about 76 tokens). That can hurt retrieval when a question needs context from two neighbouring sections. Decide at M7 (retrieval metrics on the golden set) whether to merge short adjacent sections or lower the overlap. Do not tune this before measuring.

## Chunk metadata

See `docs/data-model.md`. `Chunk.embedding_text` prepends "document title > section title" to the text, so a bare bullet still carries its context when embedded at M6.
