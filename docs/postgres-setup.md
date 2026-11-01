# PostgreSQL (M10)

**Status:** done and verified on 2026-11-01 against a native PostgreSQL 18.6 on Windows. The 4 PostgreSQL tests pass,
the whole suite ran on PostgreSQL, the development data was copied across and compared cell by cell, and the app has
been running on it since. See "What was verified" at the end.

SQLite stays the default. Switching is one setting: `DATABASE_URL`. Removing it switches back.

## 1. Install PostgreSQL (Windows)

1. Download the installer from <https://www.postgresql.org/download/windows/> (EnterpriseDB). Version 16 or newer;
   18.6 is the one tested. (`docker-compose.yml` uses 16.)
2. Install the server and command-line tools; keep port 5432. Choose a password for the `postgres` superuser
   and keep it somewhere safe. Skip Stack Builder.
3. Create the application role and two databases. Open **SQL Shell (psql)**, log in as `postgres`, and run:

```sql
CREATE ROLE apex LOGIN PASSWORD 'choose-a-long-password';
CREATE DATABASE apex OWNER apex;
CREATE DATABASE apex_test OWNER apex;   -- scratch database for the test suite
```

   Run these from a window whose prompt reads `postgres=#`. Pasting them into PowerShell does nothing. If a login
   later fails with "password authentication failed", the server log
   (`C:\Program Files\PostgreSQL\18\data\log`) says whether the role exists (`Role "apex" does not exist`) or the
   password is wrong; PostgreSQL gives the client the same message for both.

The role is an ordinary user (no superuser, no create-role, no create-database).

## 2. Point the app at it

In `.env` only (never `.env.example`; git tracks that file):

```
DATABASE_URL=postgresql://apex:choose-a-long-password@localhost:5432/apex
```

`postgres://` and `postgresql://` are accepted and use the psycopg 3 driver automatically. If the password has
special characters (`@ : / % #`), URL-encode them.

Create the tables:

```powershell
alembic -c backend/alembic.ini upgrade head
```

## 3. Move your existing SQLite data (optional)

Only needed if you want to keep the users, documents and conversations you already have. Stop the app first so
nothing writes to SQLite during the copy, then, with `DATABASE_URL` pointing at the empty, migrated PostgreSQL:

```powershell
.venv\Scripts\python.exe -m app.cli copy-db --from sqlite:///./storage/app.db
```

It refuses a target that already has rows or has no tables, copies in one transaction (a failure leaves the target
empty), keeps timestamps in UTC, advances the id counters, and checks the row counts. The uploaded files in
`storage/uploads` and the vector index in `vector_db/` are not in the database and need no copying.

To redo a copy into a database that already has rows, empty it first (`DROP SCHEMA public CASCADE; CREATE SCHEMA
public;` as the `apex` role, which owns the database) and run step 2's migration again. Take a copy of `app.db`
first and copy from the copy.

After the switch, new data exists only in PostgreSQL. The SQLite file is not written to any more.

## 4. Verify on PostgreSQL

```powershell
$env:TEST_POSTGRES_URL = "postgresql://apex:choose-a-long-password@localhost:5432/apex_test"
pytest backend/tests/integration/test_postgres.py        # migrations, schema match, copy, partial index

$env:TEST_DATABASE_URL = $env:TEST_POSTGRES_URL          # the WHOLE suite on PostgreSQL
pytest                                                   # drops every table after each test (about 7 minutes)
```

Use the scratch `apex_test` database for these, never `apex`: the full run drops every table after each test.

## What changed in the code

- `DATABASE_URL` accepts `sqlite:///` or `postgresql://`; anything else is rejected at start-up.
- PostgreSQL connections are pooled (5, up to 10 more) and checked before use (`pool_pre_ping`).
- `app.cli copy-db` and `app/services/db_copy.py`: the one-off SQLite to PostgreSQL copy.
- A test that the migrations produce exactly the schema the models describe.
- The migrations already used portable types and a `postgresql_where` partial index, so none needed changing.

## What was verified (2026-11-01, PostgreSQL 18.6)

- **Migrations 0001 to 0008** apply, downgrade to base and apply again, and the result matches the models exactly.
- **Whole suite on PostgreSQL:** 1171 passed, 2 expected failures, 1 live test deselected, 1 failed. The failure was
  a mistake in the copy test (it compared clock readings instead of instants), fixed afterwards; that file then
  passed 4 of 4 with SQLite and with PostgreSQL as the source. The full suite was not rerun after the fix. No
  application code needed changing.
- **Data copy:** 9 users, 6 department links, 8 documents, 10 conversations, 38 messages, 23 refresh tokens and 61
  audit rows, compared cell by cell with no difference. The app then ran on PostgreSQL: `/health/ready` reported the
  database ok, and a failed sign-in wrote its audit row to PostgreSQL.
- **Time zones:** PostgreSQL returns times in the server's zone (India time here), not UTC. The data is the same
  instant, and nothing in the application broke, but code that strips a time zone without converting would show the
  wrong hour.

## Known gaps

- The `apex` password should be long and random; a short one works but is easy to guess.
- No tested backup or restore of the PostgreSQL data.
- Out of scope by decision (M10 scope = Postgres migration only): document versions on the database side, recycle
  bin, library filters and pagination.
- Keyword search still scans chunks in Python (`docs/retrieval-baseline.md`); moving it into PostgreSQL full-text
  search is a separate piece of work.
- The compose file's PostgreSQL 16 container has not been run (Docker is not installed here).
