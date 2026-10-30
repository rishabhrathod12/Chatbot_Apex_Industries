# Security Test Report: M11 (2026-10-31)

Scope: login, roles, department permissions, personal privacy, and the audit trail.
Result of the full suite at the time of writing: **672 passed, 2 expected failures, 0 failed** (about 2.5 minutes). The 2 expected failures are the offline-embedder retrieval misses from M6, unrelated to security.

Run it yourself: `.venv\Scripts\pytest -q` (everything), or `pytest -q backend/tests/integration/test_access_control_matrix.py` (the permission matrix only).

## 1. What was tested

| Area | Tests | File |
|---|---|---|
| Passwords, tokens, rate limiter, permission rules | 45 | `unit/test_security_primitives.py` |
| Search filter: own departments, company-wide, narrowing, fail-closed | 8 | `integration/test_vectorstore_visibility.py` |
| Login, lockout, expiry, refresh rotation and reuse, audit of logins | 36 | `integration/test_auth_api.py` |
| **Access-control matrix**: 6 users x 5 documents | 120 | `integration/test_access_control_matrix.py` |
| Uploading, deleting, personal privacy, conversation privacy, audit of refusals, user management, account tools | 52 | `integration/test_access_control_more.py` |
| Every endpoint refuses unauthenticated requests | 5 | `integration/test_route_protection.py` |
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
