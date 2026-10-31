# PostgreSQL (M10)

**Status:** code and tests written (2026-10-31). **Not yet run against a real PostgreSQL**: none is installed on
this machine. The SQLite suite passes (856 tests); the 4 Postgres tests are skipped until you set
`TEST_POSTGRES_URL` (step 4).

SQLite stays the default for development. Switching is one setting: `DATABASE_URL`.

## 1. Install PostgreSQL 16 (Windows)

1. Download the installer from <https://www.postgresql.org/download/windows/> (EnterpriseDB), version 16.
2. Install the server and command-line tools; keep port 5432. Choose a password for the `postgres` superuser
   and keep it somewhere safe. Skip Stack Builder.
3. Open **SQL Shell (psql)** (or `psql` from `C:\Program Files\PostgreSQL\16\bin`) and run:

```sql
CREATE USER apex WITH PASSWORD 'choose-a-long-password';
CREATE DATABASE apex OWNER apex;
CREATE DATABASE apex_test OWNER apex;   -- scratch database for the test suite
```

## 2. Point the app at it

In `.env` only (never `.env.example`; git tracks that file):

```
DATABASE_URL=postgresql://apex:choose-a-long-password@localhost:5432/apex
```

`postgres://` and `postgresql://` are accepted and use the psycopg 3 driver automatically. If the password has
special characters (`@ : / %`), URL-encode them.

Create the tables:

```powershell
alembic -c backend/alembic.ini upgrade head
```

## 3. Move your existing SQLite data (optional)

Only needed if you want to keep the users, documents and conversations you already have. With `DATABASE_URL`
pointing at the empty, migrated PostgreSQL:

```powershell
.venv\Scripts\python.exe -m app.cli copy-db --from sqlite:///./storage/app.db
```

It refuses a target that already has rows or has no tables, copies in one transaction (a failure leaves the target
empty), keeps timestamps in UTC, advances the id counters, and checks the row counts. The uploaded files in
`storage/uploads` and the vector index in `vector_db/` are not in the database and need no copying.

## 4. Verify on PostgreSQL

```powershell
$env:TEST_POSTGRES_URL = "postgresql://apex:choose-a-long-password@localhost:5432/apex_test"
pytest backend/tests/integration/test_postgres.py        # migrations, schema match, copy, partial index

$env:TEST_DATABASE_URL = $env:TEST_POSTGRES_URL          # the WHOLE suite on PostgreSQL
pytest                                                   # drops every table after each test
```

Use the scratch `apex_test` database for these, never `apex`.

## What changed in the code

- `DATABASE_URL` accepts `sqlite:///` or `postgresql://`; anything else is rejected at start-up.
- PostgreSQL connections are pooled (5, up to 10 more) and checked before use (`pool_pre_ping`).
- `app.cli copy-db` and `app/services/db_copy.py`: the one-off SQLite to PostgreSQL copy.
- A test that the migrations produce exactly the schema the models describe (runs on SQLite today).
- The migrations already used portable types and a `postgresql_where` partial index, so none needed changing.

## Known gaps

- Nothing here was executed on PostgreSQL yet. Likely first-run surprises: a test that assumed SQLite behaviour
  (case-sensitive text comparison, naive datetimes) when running the full suite with `TEST_DATABASE_URL`.
- Out of scope by decision (M10 scope = Postgres migration only): document versions, recycle bin, library
  filters and pagination.
- Keyword search still scans chunks in Python (`docs/retrieval-baseline.md`); moving it into PostgreSQL full-text
  search is a separate piece of work.
