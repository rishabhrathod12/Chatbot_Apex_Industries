# Security Test Report

**Contents:** (A) the authorization sweep after the authentication redesign, then (B) the original M11 report.

## A. Authorization sweep after the authentication redesign (2026-10-31)

Scope: portals and Login IDs, temporary passwords, admin tools, company document archive and versions, old-conversation citations, and the employee and Super Admin workspaces, all attacked from the other side. Result of the full suite: **1145 passed, 4 skipped (Postgres), 2 expected failures (M6), 0 failed**, ruff and mypy clean. The sweep itself is 111 tests, about 50 seconds.

Run it: `pytest -q backend/tests/security/test_authorization_sweep.py backend/tests/security/test_authorization_changes.py`

### What the sweep attacks

| Attack | How | Result |
|---|---|---|
| An endpoint added without anyone deciding who may call it | every route in the live API description must be in an explicit list (public, usable with a temporary password, any signed-in person, or Super Admin only) | the test fails for any unlisted or stale route |
| Admin pages used by anyone else | every `/users` and `/admin` route, called as two managers and two employees | 403 `not_allowed` before the request body is even read; 401 without a token |
| A private document, touched by everyone else | 7 endpoints (open, history, re-index, archive, restore, new version, delete) x 6 attackers (a colleague, another department, both managers, two Super Admins) | all 42 get the **same answer as a document that does not exist** (404, identical body); the document is unchanged afterwards and still found by its owner |
| Private documents in other libraries | the library listing of every other person | never listed |
| Forged search and chat fields | `owner_id`, `user_id`, `document_ids`, `departments`, `include_company_wide`, `filter`, `search_filter`, `role`, sent with personal and company scope | results and cited sources never include another person's private document or a forbidden department; the language model's prompts (captured and inspected) never contain their text |
| The department box | `quality`, `QUALITY`, ` quality `, `Quality Control`, `qc` as a forbidden department | nothing from it, or a 422 |
| A Super Admin reading private documents | company search and chat as each Super Admin | private documents never appear (the documented policy); company documents they may read still do |
| Someone else's conversation | read, delete and continue by id | 404 each time; nothing is added to it |
| A temporary password | every signed-in and admin route | 403 `password_change_required`, except the way out |
| A disabled account | every route, with a token issued before it was disabled | 401 everywhere; refresh and sign-in also refused |
| A demotion or promotion | the old token, on the very next request | admin powers gone at once; manager powers granted at once and only for their department |
| Company uploads | an employee and a manager, to each of 6 departments, with and without company-wide visibility | employees: 403 for all 12; a manager: only their own department |
| Fields a client should not set | extra fields on user update and create (login ID, password hash, e-mail, creator, the forced-change flag, id) | ignored; an admin-created account always has to change its password |
| Dodging the login lockout | rotating `X-Forwarded-For`, `X-Real-IP`, `Forwarded` | still locked out |
| Closing the first-time setup | posting to it once anyone exists | 409 |
| The audit trail | refused attempts by a colleague | recorded as "denied"; the whole log contains no document text, password or token |

The read-only attacks run against the settings of a real deployment (keyword plus meaning search, then the reranking check), because those paths read the stored text directly. The earlier M11 matrix runs in the offline test setup.

### Do these tests really catch mistakes? (mutation check)

Each line below was deliberately broken in the real code, the sweep run, and the code restored. **All 11 were caught.**

| Deliberate mistake | Caught by |
|---|---|
| Anyone may read any personal document | the private-document tests |
| Personal search not limited to its owner | the forged-fields and prompt tests |
| The Super Admin check removed | the admin-route test |
| The forced password change skipped | the temporary-password test |
| The department limit dropped from the search filter | the forged-fields and prompt tests |
| A manager may add to any department | the upload tests |
| Disabled accounts still accepted | the disabled-account test |
| Anyone may open anyone's conversation | the conversation test |
| The client controls the forced-change flag | the forged-fields test |
| The login lockout trusts `X-Forwarded-For` | the lockout test |
| A new, unclassified endpoint appears | the route classification test |

Three earlier-phase checks were repeated: archived documents readable by everyone, old citations never redacted, and an unreadable document kept in a saved conversation. All caught.

### Findings

- **No new security defect was found**: every sweep test passed on its first run. That is a result, not proof. It means the checks above hold, not that nothing else can go wrong.
- My first version of the sweep rebuilt the whole company for every test and took 3 minutes. The attacks that change nothing now share one company (about 50 seconds in total).

### Not covered, on purpose or by limit

- **The web pages in a real browser.** They were driven by Streamlit's test runner against a fake API; nobody has looked at them in a browser yet.
- **Answer text in old conversations** is stored as the person saw it. Citations are blanked when access is lost; the text of the answer is not rewritten.
- **PostgreSQL.** The sweep and the rest of this report were written and first run on SQLite. On 2026-11-01 the whole suite, including the 111-test sweep, was run again on PostgreSQL 18.6 with no security test failing (the one failure in that run was a data-copy test, unrelated to access control). The deliberate-break checks were not repeated on PostgreSQL.
- **Several API processes.** The login limiter and the last-Super-Admin lock are in memory (one process).
- **TLS, and a stolen but still valid access token** (valid for at most 30 minutes; refresh tokens can be revoked).
- **Prompt injection inside a permitted document** is a model-behaviour risk, handled by the answering rules, not by access control.

---

## B. M11 report (2026-10-31)

# Security Test Report: M11 (2026-10-31)

Scope: login, roles, department permissions, personal privacy, and the audit trail.
Result of the full suite at the time of writing: **672 passed, 2 expected failures, 0 failed** (about 2.5 minutes). The 2 expected failures are the offline-embedder retrieval misses from M6, unrelated to security.

Run it yourself: `.venv\Scripts\pytest -q` (everything), or `pytest -q backend/tests/security/test_access_control_matrix.py` (the permission matrix only).

## 1. What was tested

| Area | Tests | File |
|---|---|---|
| Passwords, tokens, rate limiter, permission rules | 45 | `security/test_security_primitives.py` |
| Search filter: own departments, company-wide, narrowing, fail-closed | 8 | `integration/test_vectorstore_visibility.py` |
| Login, lockout, expiry, refresh rotation and reuse, audit of logins | 36 | `security/test_auth_api.py` |
| **Access-control matrix**: 6 users x 5 documents | 120 | `security/test_access_control_matrix.py` |
| Uploading, deleting, personal privacy, conversation privacy, audit of refusals, user management, account tools | 52 | `security/test_access_control_more.py` |
| Every endpoint refuses unauthenticated requests | 5 | `security/test_route_protection.py` |
| Interface: sign-in, sign-out, role-based pages and buttons, token renewal | 50 | `unit/test_frontend.py` |

## 2. The matrix (real users, real tokens, real indexed documents)

Users: Super Admin; Safety, HR and Production managers; a Production employee; a Stores employee (Stores has no documents).
Documents: Safety manual (company-wide), HR policy (company-wide), Production manual, Maintenance manual and Quality SOP (department-only).

For every user and document the tests check, over HTTP:

| Check | Expectation | Result |
|---|---|---|
| Library listing | exactly the documents they may read | pass for all 6 users |
| Opening a document by id | 200 if readable, 404 if not (same answer as "does not exist") | pass for all 30 pairs |
| Search | never returns a chunk from a forbidden document, and still finds every permitted one | pass for all 6 users and 5 queries each |
| What the language model is sent | prompts captured and inspected: no text from a forbidden document ever reaches the model | pass for all 6 users |
| Cited sources in answers | never include a forbidden document | pass |
| The department box | asking for another department returns nothing, narrowing to an allowed one works | pass |
| Re-indexing | 202 for managers of that department and admins; 403 if you can read but not change; 404 if you cannot see it | pass for all 30 pairs |
| User management | only the Super Admin | pass |

Visible per user: Admin all 5; Safety, HR and Stores users 2 (Safety + HR); Production manager and employee 3 (Safety, HR, Production). Nobody outside Quality sees the Quality SOP, and nobody outside Maintenance sees the Maintenance manual.

## 3. Do the tests really catch mistakes? (mutation check)

A test suite that never fails proves little. I deliberately broke the permission rules in seven different ways and checked the tests failed each time. The original file was restored byte-for-byte afterwards.

| Deliberate mistake | Caught | First failing test |
|---|---|---|
| Everyone treated as admin when searching | yes (27 failures) | answers cite forbidden documents |
| `can_read_document` always true | yes | opening a document directly |
| Company-wide documents stop being visible | yes | search no longer finds permitted documents |
| `can_manage_document` always true | yes | re-index permissions |
| The department box widens instead of narrowing | yes | asking for another department reveals nothing |
| A manager can manage any department | yes | re-index permissions |
| A Super Admin can read personal documents | yes | personal privacy |

I did the same for the guard on public endpoints: adding an unprotected `/leaky` endpoint made `test_route_protection.py` fail with "GET /api/v1/leaky -> 200", and removing it made it pass again.

## 4. Defects the tests found while building M11

1. **Changing a user's departments to an overlapping set crashed with a 500.** The code cleared the list and re-added everything, so the database saw a duplicate before the delete. Fixed: only the difference is written. (`test_changing_a_departments_takes_effect_at_once`)
2. **The first version of the "every route is protected" test checked nothing.** Newer FastAPI nests included routers, so walking `app.routes` found zero routes. A "at least 19 routes" sanity check exposed it, and the test now reads the API's own description. Without that check it would have passed vacuously.
3. **A stale old API process** (from before login existed) kept running after I tried to stop it with a Unix-style command, and answered unauthenticated requests. Not a code defect, but a reminder: stop servers by port on Windows. The new server returned 401 for the same request.

## 5. Checked in a real browser (Edge driven by Playwright, real OpenAI models)

| Person | Library | Result |
|---|---|---|
| Production employee | 4 documents | Safety question answered with a source. The Quality question ("Who can release a product after final inspection?") answered "I couldn't find this in the documents you have access to", although the Quality SOP contains the answer |
| Stores employee | 2 documents (company-wide only) | Asked about CNC doors (a Production document): not found |
| Safety manager | 2 documents | Re-index and Delete buttons on the Safety document only, none on HR |
| Super Admin | 6 documents | Sees the Users page, and has no Disable button on their own row |
| Anyone, wrong password | | "That email and password do not match an active account." |

These browser checks use a throwaway Playwright installation and are not part of the automated suite.

## 6. Not tested, or only partly

- **Load and timing.** Only the dummy-hash approach is used to keep login timing similar. I did not measure timing differences.
- **Several API processes.** The login limiter and the single embedded vector folder assume one process.
- **Real-world tokens in cookies.** The interface keeps tokens in server memory per session; there is no cookie handling to test.
- **HTTPS, CORS and secure headers** belong to deployment (M14).
- **Penetration testing by a person.** None done. These are automated tests written by the same author as the code, so they reflect my understanding of the threats.
- **Questions that name a forbidden document in words** (for example "what does the Quality SOP say?") are answered "not found", but the user's wording is still sent to the model and to OpenAI. That is by design, since only retrieved text is restricted.
