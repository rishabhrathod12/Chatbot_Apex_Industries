# Security Architecture and Threat Model

## 1. Assets

- Enterprise documents (per department), personal documents (per user)
- User credentials and tokens
- OpenAI API key and JWT signing secret
- Conversation history

## 2. Threats and controls

| Threat | Control | Verified by |
|---|---|---|
| User reads another department's documents through search | Server-built `AccessFilter` from JWT, applied in vector AND keyword search, re-checked against Postgres | Role x department x document matrix test |
| User reads another user's personal documents or chats | `owner_id` filter, ownership check on every document and conversation endpoint | Cross-user tests |
| Client tampers with the department filter | Filter is never accepted from request parameters | Test: filter params are ignored/rejected |
| Forged or expired JWT | Signature check, short expiry, refresh tokens | Tampered/expired token tests |
| Malicious upload (renamed exe, huge file, path traversal) | Extension + MIME + magic-byte check, size/page caps, random server filenames, storage outside any served path | Upload attack tests |
| Prompt injection inside a document | Context delimited and treated as data, fixed system prompt, injection cases in eval set | Behaviour eval |
| Hallucinated answer shown as fact | Relevance gate, grounded prompt, citations from metadata, faithfulness eval | RAGAS + golden set |
| Secrets in git or logs | `.env` ignored, `.env.example` placeholders, gitleaks pre-commit, log redaction | Scan + log test |
| Sensitive text in logs | Log IDs, user, latency, token counts only. Never questions, answers or document text | Log content test |
| Brute-force login | Rate limiting, generic error message | Test |
| Endpoint added without auth | Auth is a required dependency on the router, plus a test that lists every route | Route-audit test |

## 3. Roles

| Role | Capabilities |
|---|---|
| Super Admin | Manage users, departments and all documents. Can query all enterprise documents |
| Department Manager | Upload/update/delete documents in own department. Query own department + company-wide |
| Employee | Query own department + company-wide documents. Manage own personal documents |

Exact cross-department rules (for example whether Safety content is company-wide) are an open question, see ADR-0001 Pending.

## 4. Data residency note

Document text and questions are sent to OpenAI for embeddings and generation. This is acceptable for the fictional sample data. Real company data would need a policy decision.

## 5. Rules for all code

1. No secrets in source or git.
2. One function builds the access filter. No endpoint builds its own.
3. Never filter after the LLM has seen the content.
4. Never log document text, questions or answers.
5. Treat every uploaded file as untrusted.
