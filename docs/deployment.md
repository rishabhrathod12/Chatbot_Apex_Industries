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

## Before letting other people use it (not done)

- **TLS.** Nothing here encrypts traffic. Put a reverse proxy (Caddy or nginx) in front for HTTPS and publish only
  that. Passwords and tokens must not cross a network in clear text.
- **One API worker only.** The embedded vector store (Qdrant local mode) can be opened by one process. Scaling
  out needs a Qdrant server and a shared store for the login rate limiter (currently in memory); both are
  separate changes.
- **Login rate limiting sees the web container's address**, not the user's, because Streamlit calls the API from
  the server. It still limits per email, but not per client machine.
- **Backups and restore** are not automated or tested. At minimum: `docker compose exec db pg_dump -U apex apex > apex.sql`
  plus a copy of the `uploads` and `vectors` volumes.
- **Updates:** `git pull` then `docker compose up -d --build`; migrations run on start. Take a backup first.
- No CI pipeline yet.
