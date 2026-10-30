# Security Architecture

Status after M11. See `docs/security-test-report.md` for how each control is tested.

## 1. What we protect

- Company documents, split by department, and each user's personal documents
- Conversations (questions and answers)
- Accounts, passwords and login sessions
- The OpenAI key and the token-signing secret

## 2. Who can do what

| | Super Admin | Department Manager | Employee |
|---|---|---|---|
| Read company documents | all | own department(s) + company-wide | own department(s) + company-wide |
| Add company documents | any department | own department(s) only | no |
| Mark a document company-wide | yes | yes, for own department(s) | no |
| Re-index or delete company documents | all | own department(s) only | no |
| Personal documents and conversations | own only | own only | own only |
| Manage users | yes | no | no |

- **Company-wide** documents are readable by every signed-in user. In the sample set, the Safety manual and HR policy are company-wide; Production, Maintenance and Quality documents are department-only.
- **Personal documents and conversations are private to their owner, even from a Super Admin.**
- A user with no departments sees only company-wide documents.
- The department box in the interface can only **narrow** a search. It never reveals anything the user could not already see.

All of these rules live in one module, `backend/app/core/permissions.py`. Nothing else decides access.

## 3. Controls

| Threat | Control | Tested in |
|---|---|---|
| Reading another department's documents through search | One function builds the search filter from the logged-in user (`build_search_filter`). It is applied inside the vector search, so forbidden chunks are never retrieved | `test_access_control_matrix.py`, `test_vectorstore_visibility.py` |
| Forbidden text reaching the language model | The model only receives chunks the filter allowed. Tests capture every prompt sent and check no forbidden document appears | `test_access_control_matrix.py` |
| Probing for document or conversation ids | Anything you may not read returns 404, identical to "does not exist" | matrix and `test_access_control_more.py` |
| A user changing documents they should not | Reading and changing are checked separately: 404 if you cannot see it, 403 if you can see it but not change it | matrix, `test_access_control_more.py` |
| Forged, tampered or expired tokens | HS256 with a fixed algorithm list, issuer and type checks, required claims. `alg: none`, wrong key, wrong issuer and refresh-type tokens are rejected | `test_security_primitives.py`, `test_auth_api.py` |
| Stale permissions | A token holds only the user id. Role and departments are read from the database on every request, so changes (and disabling an account) apply immediately | `test_auth_api.py`, `test_access_control_more.py` |
| Password guessing | Failed attempts per email and address are limited (5 per 15 minutes by default). Lockout applies even to the correct password | `test_auth_api.py` |
| Learning which emails exist | Wrong email, wrong password and disabled account give identical answers and take the same time (a dummy hash is checked for unknown emails) | `test_auth_api.py` |
| Stolen refresh token | Refresh tokens are single-use and rotate. Presenting an already-used one ends all of that user's sessions. Only a hash of the token is stored | `test_auth_api.py` |
| Leaked password database | scrypt, per-password random salt, cost stored in the hash. No plain text anywhere | `test_security_primitives.py` |
| An endpoint added without a login check | A test sends a real unauthenticated request to every endpoint listed in the API's own description and expects 401, except the four public ones | `test_route_protection.py` |
| Locked-out admins | Admins cannot demote or disable themselves | `test_access_control_more.py` |
| Hostile uploads | Extension, content and size checks; random on-disk names; storage outside the web root (M3) | M3 tests |
| Prompt injection from documents | Excerpts are wrapped and tag-neutralised, treated as data (M7) | M7 tests |
| Model output used to leak data | Markdown images are stripped and link addresses removed before display (M9) | `test_frontend.py` |
| Secrets in git or logs | `.env` is ignored. A test fails if a key has a value in `.env.example`. Logs hold ids and counts only | `test_repo_hygiene.py`, M2 tests |
| Interactive API docs exposing endpoints | Not served when `APP_ENV=production` | `test_route_protection.py` |

Public endpoints (no login): `GET /health`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`. Everything else requires a valid access token.

## 4. Audit trail

Table `audit_log` records who did what and whether it was allowed: logins (success, failure, lockout), refresh-token reuse, uploads, re-indexing, deletes, user changes, and **every refused action** (opening a document you cannot read, deleting one you cannot change, adding to a department you do not manage, calling an admin endpoint).

It stores ids and codes only. Failed logins store a one-way fingerprint of the typed email, never the email itself, because people sometimes type their password into that box.

## 5. Accounts

There is no built-in account and no default password. Create the first Super Admin on the command line (`python -m app.cli create-user ...`). Passwords are typed at a hidden prompt or read from an environment variable and are never printed. Minimum length 12. `seed-demo` creates six demo users with one generated password and refuses to run in production.

## 6. Known limits

- **The login limiter is in memory**, so it works for one API process. With several processes it needs a shared store (M14).
- **Tokens are kept in server memory per browser session in the Streamlit app**, so reloading the page signs you out. A cookie-based session would fix that.
- **Saved conversations are snapshots.** If a user later loses access to a document, their old answers that quote it remain visible to them.
- **Documents indexed before M11 lack the `visibility` marker** in the search index. They fail closed (treated as department-only) until re-indexed.
- **No two-factor login, no password-reset-by-email, no single sign-on.** A Super Admin resets passwords.
- **Document text and questions go to OpenAI** for embeddings and answers. Fine for the fictional sample data; real company data needs a policy decision.
- **SQLite and a single process** are assumed until M10 and M14.

## 7. Rules for all code

1. No secrets in source or git.
2. Access rules live only in `core/permissions.py`.
3. Never filter after the language model has seen the content.
4. Never log document text, questions, answers, passwords or tokens.
5. Treat every uploaded file and every model answer as untrusted.
6. New endpoints depend on `get_current_principal` (the route-protection test enforces this).
