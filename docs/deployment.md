# Deployment with Docker (M14)

**Status:** files written and checked statically (2026-10-31). **Nothing has been built or run**: Docker is not
installed on this machine. Expect small first-run fixes. The application changes (health checks, security
headers) are tested and pass.

## What runs

| Service | Image | Port (this computer only) | Notes |
|---|---|---|---|
| `db` | `postgres:16-alpine` | none published | data in volume `pgdata` |
| `api` | built from `Dockerfile` | 127.0.0.1:8000 | applies migrations on start, then one uvicorn worker |
| `web` | same image, Streamlit | 127.0.0.1:8501 | talks to `api` over the compose network |

Uploaded files (`uploads` volume) and the vector index (`vectors` volume) live outside the database.
**Back up all three volumes together**: a database restored without its matching uploads and vector index
points at files that are not there.

## First start

1. Install Docker Desktop (WSL2 backend) and confirm `docker compose version` works.
2. `copy .env.example .env`, then set these in `.env` (never in `.env.example`):
   - `POSTGRES_PASSWORD` : a long random value using only URL-safe characters, for example
     `python -c "import secrets; print(secrets.token_urlsafe(32))"`. (It is placed inside a URL; `@ : / %` break it.)
   - `JWT_SECRET_KEY` : 32+ characters, same generator.
   - `OPENAI_API_KEY`.
3. `docker compose up -d --build`. Compose refuses to start if any of the three is missing.
4. Wait until `docker compose ps` shows all three services **healthy**.
5. Create the first Super Admin (the password is typed at a hidden prompt):

```powershell
docker compose exec api python -m app.cli create-user --email you@company.com --name "Your Name" --role super_admin
```

6. Open <http://localhost:8501>.

Moving existing data from your SQLite development database: see `docs/postgres-setup.md` step 3
(`docker compose exec` with the SQLite file mounted, or run `copy-db` from your own machine against the
published database port temporarily).

## Health and monitoring

- `GET /api/v1/health` : liveness, the process answers.
- `GET /api/v1/health/ready` : readiness. Checks the database and that the upload and vector folders are writable.
  Returns 503 with `ok`/`failed` per check, never error details. Docker uses it for the `api` health check.
- Logs are structured JSON on stdout with a request id, passwords/tokens/keys redacted. Compose rotates them
  (10 MB x 5 files per service). Read with `docker compose logs -f api`.
- Every response has an `X-Request-ID`; quote it when reporting a problem and search the log for it.
- Not included: metrics (Prometheus), alerting, log shipping. Add them when there is something to alert.

## Hardening already in place

- Containers run as a non-root user; the image holds no `.env` or data (`.dockerignore`, tested).
- Secrets are required from `.env` and are not in any committed file (tested).
- Ports are published on 127.0.0.1 only; the database is never published.
- `APP_ENV=production` turns off `/docs`, refuses the offline test embedder, and requires a JWT secret.
- API responses send `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store`
  and a deny-everything Content-Security-Policy.

## Backup and restore (native install, tested 2026-11-01)

`scripts\backup.py` (run from the project folder with `.venv\Scripts\python.exe`):

- `backup` writes `backups\apex-<date>-<time>\` with the database (`database.dump`, or `database.sqlite` for SQLite),
  `uploads.zip`, `vector_db.zip` and a `manifest.json` (row count per table, migration version, checksums). It deletes
  nothing unless you pass `--keep N`. The database password comes from `.env` through the environment and is never
  printed.
- `restore --backup <folder> --into-db apex_test` restores the database into a different database on the same server and
  compares every table's row count with the manifest. A damaged file (checksum mismatch) is refused before anything
  is restored, and restoring over the live database needs `--replace-live`. `--files-to <folder>` also unpacks the
  uploads and vector index.
- Tested: a real backup of the live database restored into the scratch database with every table matching, and
  deliberately corrupted files were refused. The vector index is copied as files and may be inconsistent if the API is
  busy; it can be rebuilt from the uploads.
- **Nightly job (written and tested, not registered).** `scripts\backup_job.ps1 -Destination "F:\ApexBackups;..."` takes a
  backup, copies it to each destination (folders separated by semicolons), checks every copied file against the
  checksums in `manifest.json`, and appends a line to `backups\backup.log` (times and results, never passwords). It
  exits with 1 if anything failed. It deletes nothing unless you pass `-Keep N`. `scripts\register_backup_task.ps1
  -Destination "..." -Time 02:30` registers it as a daily Task Scheduler job that runs as you while you are signed in
  (no stored password) and catches up at the next sign-in if the time was missed; add `-DryRun` to see what it would
  do, or `-Remove` to take it out. Check it with `Get-ScheduledTaskInfo -TaskName ApexBackup` and the log.
- **Where the copy must go.** On the development machine D:, E: and F: are three partitions of one hard disk, and the
  PostgreSQL data is on the small SSD (C:). A copy on D: or F: protects against deleting or damaging a file, not
  against that disk failing. A real second copy needs a USB drive, a network share or cloud storage.
- **Locked files.** While the API runs, Windows keeps `vector_db\.lock` open. The backup always leaves that marker
  out (the vector store recreates it) and, for any other locked file in the vector index, records it in the manifest
  (`vector_index_skipped`) and prints a warning instead of failing: the index can be rebuilt from the uploads. A
  locked uploaded file does stop the backup, because it cannot be rebuilt. A failed backup removes its own half-written
  folder.
- Tested 2026-11-01 with the API running: a normal run, one good plus one unreachable destination (exit 1, the good
  copy still made), a tampered copy (one flipped byte, and a deleted file, both caught), and a restore of the
  resulting backup into the scratch database (every table matched). The scheduled task itself has not been registered
  or run.
- To recover the live database after a disaster, restore with `--replace-live` into an empty `apex` database, then
  copy the uploads and vector index back. That full recovery has not been rehearsed; the restore into the scratch
  database has.

## Before letting other people use it (not done)

- **TLS.** Nothing here encrypts traffic. Put a reverse proxy (Caddy or nginx) in front for HTTPS and publish only
  that. Passwords and tokens must not cross a network in clear text.
- **One API worker only.** The embedded vector store (Qdrant local mode) can be opened by one process. Scaling
  out needs a Qdrant server and a shared store for the login rate limiter (currently in memory); both are
  separate changes.
- **Login rate limiting sees the web container's address**, not the user's, because Streamlit calls the API from
  the server. It still limits per email, but not per client machine.
- **Backups are not scheduled yet, and nothing copies them off this machine.** The manual procedure and the nightly job are written and tested (above); register the job once you have chosen a destination.
  A backup on the same disk as the data does not survive a disk failure: copy `backups\` to another drive or machine
  on a schedule (for example a Windows Task Scheduler job running `scripts\backup.py backup`).
  For the Docker setup, the equivalent is `docker compose exec db pg_dump -U apex apex > apex.sql` plus a copy of
  the `uploads` and `vectors` volumes; that route has not been run.
- **Updates:** `git pull` then `docker compose up -d --build`; migrations run on start. Take a backup first.
- **CI** is defined in `.github/workflows/ci.yml` (ruff and mypy pinned to the versions the project uses, the full suite on SQLite, and the 4 PostgreSQL tests against a PostgreSQL 18 service). Its first run on GitHub (2026-11-01, commit `b451e97`) passed all three jobs on Linux. It does not run the live OpenAI tests, the whole suite on PostgreSQL, or a secret scan.
