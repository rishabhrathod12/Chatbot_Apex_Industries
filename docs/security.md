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
| Keyword search or reranking becoming a side door | Keyword search scans only the chunks the user's filter allows, so even its word statistics come from permitted text. The reranker only sees chunks that passed the filter and can only drop or reorder them. The matrix is re-run in both modes and inspects every prompt sent to either model | `test_access_control_hybrid.py`, `test_hybrid_retrieval.py` |
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

The Super Admin can read the log and dashboard numbers through `GET /api/v1/admin/audit` (paged, filterable by action, outcome, person and date; filters are validated before they reach the database) and `GET /api/v1/admin/stats`. Both are Super Admin only (403 otherwise). Account changes are recorded as separate actions (`user_activate`, `user_deactivate`, `user_role_change` with a `role:` detail such as `role:super_admin`, `user_departments_change`, `user_update`); a request that changes nothing records nothing. **Personal documents stay private even here:** dashboard counts, recent uploads and recent activity cover company documents only, and an audit row about someone's personal document shows "a personal document", never its file name.

### Company document details and archiving

- A company document can be uploaded with a title, description, type (sop, manual, policy, form, checklist, report, other) and version. Title and version are stored apart from what is read from the file, so they survive re-indexing, and they are what citations show. Blank means "not given"; details are refused for personal documents, and a title or version needs a single file.
- **Archive** (`POST /documents/{id}/archive`) removes the document's vectors first, then marks it archived, so it cannot be searched, retrieved or cited by anyone; the file and details are kept. It is allowed to the people who can manage the document (Super Admin, or the manager of its department) and is audited (`document_archive`, `document_restore`, including denied attempts). Personal documents cannot be archived (they are deleted by their owner). **Restore** indexes it again; it is searchable once it shows as ready. An indexing task that was already queued when the document was archived does nothing.
- An archived document is visible only to those who can manage it; for everyone else it looks like it does not exist (404), and it is left out of listings and dashboard counts.

### New versions of company documents and old conversations

- **Replace** (`POST /documents/{id}/versions`, same managers as archive) uploads a new file for a company document. It keeps the department and visibility and inherits title, description and type unless new ones are given (the old version label is not inherited, and the same label is refused). The new file is indexed in the background and the old version stays searchable **until the new one is ready**, then is archived, so there is never a moment with neither. If the new file cannot be indexed, or the old version's vectors cannot be removed, nothing is taken away: the old version stays in use (the second case leaves both searchable, and is audited as `old_version_kept`). The same file, or a file that is already another document in the library, is refused. `GET /documents/{id}/versions` lists the chain, leaving out versions the person may not see. Audited as `document_version_upload` and `document_version_replace`.
- **Old conversations:** when a saved conversation is opened, a citation to a document the person can no longer read (archived, deleted, moved to another department, or never theirs) is replaced by a blank placeholder (`available: false`, no title, section, quote or id) that keeps its `[n]` number. **Limit:** the answer text itself is stored as it was and is not rewritten, so it can still contain what the person was told at the time.

## 5. Accounts

There is no built-in account and no default password. The first Super Admin is created either on the command line (`python -m app.cli create-user ...`) or on the **First-time setup** form that the sign-in page shows while no user exists. That form is the one unauthenticated way to create an account, so: it works only while the users table is empty (then it is closed for good, `409 setup_already_done`); in production it is off unless `SETUP_TOKEN` is set in `.env`, and the person must type that token (compared in constant time, wrong guesses rate limited and audited as `setup_admin`/`denied`); in development the token is optional. Anyone who can reach a fresh, empty production system before you do could claim it if they knew the token, so set a long random `SETUP_TOKEN`, create the account straight away, and remove the token afterwards. Passwords are typed at a hidden prompt or read from an environment variable and are never printed. Minimum length 12. `seed-demo` creates six demo users with one generated password and refuses to run in production.

### Temporary passwords and changing a password

- A Super Admin creates an account without typing a password: the server generates one (`secrets.token_urlsafe`, never shorter than the minimum length) and returns it **once**, in that response only. It is hashed before anything is saved, so it is not in the database, the audit log or any log (tested), and it cannot be looked up later. If it is lost, the admin resets it.
- Accounts created or reset by an admin carry `must_change_password`. Until the person chooses their own password the server answers `403 password_change_required` to every endpoint except `/auth/me`, `/auth/change-password` and sign out. This is one dependency (`get_current_principal`), so a new endpoint cannot forget it, and it also applies to a Super Admin with a temporary password. Accounts made by their owner (command line, first-time setup) are not forced.
- `POST /auth/change-password` needs the current password (wrong guesses count toward a lockout, answered 422 not 401 so the app does not mistake it for an expired session), refuses a too-short or unchanged password, ends all other sessions, and returns a new one. A reset also ends the person's sessions. Both are audited (`password_change`, `password_reset`) without secrets.
- New Login IDs are made from the name (`Esha Production` becomes `esha.production`, numbered if taken) unless the admin picks one. Demo users from `seed-demo` are development-only and are not forced to change.

### Sign-in portals and the last Super Admin

- The sign-in page can send `portal: "employee" | "admin"`. The server compares it with the role stored in the database (admin page: Super Admin only; employee page: employees and department managers). A mismatch is refused with the same `401 invalid_credentials` as a wrong password, counts toward the lockout, and is audited (`login`/`failed`, target type `portal`). It is checked only after the password is correct. **The portal can only make the server refuse a login; it never grants a role.** Extra fields such as `role` in the request are ignored. Leaving `portal` out skips the check (older clients, scripts); it is not a way to gain access.
- Nobody can disable or demote the last active Super Admin. Admins also cannot demote or disable themselves. The check and the change happen under one lock, so two admins removing each other at the same moment cannot leave the company with none (`409 last_super_admin`).

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
