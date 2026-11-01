"""Back up the database, the uploaded files and the vector index; test that a backup restores.

    .venv\\Scripts\\python.exe scripts\\backup.py backup
    .venv\\Scripts\\python.exe scripts\\backup.py restore --backup backups\\apex-DATE-TIME `
        --into-db apex_test

backup   Writes a folder backups/apex-<date>-<time>/ holding the database (database.dump for
         PostgreSQL, database.sqlite for SQLite), uploads.zip, vector_db.zip and manifest.json
         (table row counts, sizes, checksums). Then checks the database file can be read back.
         Nothing is ever deleted unless you pass --keep N (keep only the newest N backups).
restore  Restores the database of a backup into ANOTHER database on the same server, then compares
         every table's row count with the manifest. It refuses to touch the live database unless
         you also pass --replace-live. Use it to prove a backup works (--into-db apex_test).
         --files-to DIR also unpacks the uploads and vector index there.

The database password is read from .env and handed to the PostgreSQL tools through the environment.
It is never printed and never appears on a command line.

A backup in the same folder, or on the same disk, as the data does not survive a dead disk. Copy the
backups folder to another drive or machine regularly.

The vector index (vector_db.zip) is copied as files. If the API is running while you back up, that
copy may not be consistent. It can always be rebuilt from the uploads (re-index), so the database
and the uploads are the parts that matter.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import get_engine  # noqa: E402


def _tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidates = sorted(Path("C:/Program Files/PostgreSQL").glob(f"*/bin/{name}.exe"))
    if not candidates:
        raise SystemExit(f"Cannot find {name}. Install the PostgreSQL command-line tools.")
    return str(candidates[-1])


def _pg_args(url: URL, database: str | None = None) -> tuple[list[str], dict[str, str]]:
    """Connection flags without the password, plus an environment that carries it."""
    args = [
        "-h",
        url.host or "localhost",
        "-p",
        str(url.port or 5432),
        "-U",
        url.username or "",
        "-d",
        database or url.database or "",
    ]
    return args, dict(os.environ, PGPASSWORD=url.password or "")


def _run(command: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        # The tools do not echo the password, but keep the output short anyway.
        raise SystemExit(f"{Path(command[0]).stem} failed:\n{result.stderr.strip()[-800:]}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _zip_folder(folder: Path, target: Path) -> int:
    """Zip a folder (an empty zip if it does not exist). Returns the number of files."""
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        if folder.is_dir():
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(folder).as_posix())
                    count += 1
    return count


def _table_counts(url: URL | str) -> dict[str, int]:
    engine = get_engine(url if isinstance(url, str) else url.render_as_string(hide_password=False))
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in sorted(inspect(engine).get_table_names()):
            counts[table] = conn.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
    return counts


def _alembic_version(url: URL) -> str | None:
    engine = get_engine(url.render_as_string(hide_password=False))
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    return row[0] if row else None


def backup(args: argparse.Namespace) -> int:
    settings = get_settings()
    url = make_url(settings.database_url)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_root = Path(args.out_dir)
    if not out_root.is_absolute():
        out_root = ROOT / out_root
    dest = out_root / f"apex-{stamp}"
    dest.mkdir(parents=True)

    files: dict[str, dict[str, object]] = {}
    if url.get_backend_name() == "postgresql":
        kind, db_file = "postgresql", dest / "database.dump"
        flags, env = _pg_args(url)
        _run([_tool("pg_dump"), *flags, "-Fc", "-f", str(db_file)], env)
        _run([_tool("pg_restore"), "--list", str(db_file)], env)  # the archive can be read back
    else:
        kind, db_file = "sqlite", dest / "database.sqlite"
        source = sqlite3.connect(url.database or "")
        target = sqlite3.connect(db_file)
        with target:
            source.backup(target)  # a consistent copy even while the app is running
        ok = target.execute("PRAGMA integrity_check").fetchone()[0]
        source.close()
        target.close()
        if ok != "ok":
            raise SystemExit(f"The SQLite copy failed its integrity check: {ok}")

    upload_dir = Path(settings.upload_dir)
    vector_dir = Path(settings.vector_store_path)
    upload_dir = upload_dir if upload_dir.is_absolute() else ROOT / upload_dir
    vector_dir = vector_dir if vector_dir.is_absolute() else ROOT / vector_dir
    n_uploads = _zip_folder(upload_dir, dest / "uploads.zip")
    n_vectors = _zip_folder(vector_dir, dest / "vector_db.zip")

    sizes: dict[str, int] = {}
    for path in (db_file, dest / "uploads.zip", dest / "vector_db.zip"):
        sizes[path.name] = path.stat().st_size
        files[path.name] = {"bytes": sizes[path.name], "sha256": _sha256(path)}
    counts = _table_counts(url)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "database_kind": kind,
        "database_name": url.database if kind == "postgresql" else Path(url.database or "").name,
        "migration_version": _alembic_version(url),
        "table_counts": counts,
        "upload_files": n_uploads,
        "vector_index_files": n_vectors,
        "files": files,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Backup written to {dest}")
    for name, size in sizes.items():
        print(f"  {name:<16} {size / 1024:>9.1f} KB")
    print(f"  {len(counts)} tables, {sum(counts.values())} rows")
    print(f"  {n_uploads} uploaded files, {n_vectors} vector index files")
    if args.keep:
        old = sorted(p for p in out_root.glob("apex-*") if p.is_dir())[: -args.keep]
        for folder in old:
            shutil.rmtree(folder)
            print(f"  removed old backup {folder.name} (--keep {args.keep})")
    print("Copy the backups folder to another drive or machine; one disk is not a backup.")
    return 0


def restore(args: argparse.Namespace) -> int:
    settings = get_settings()
    live = make_url(settings.database_url)
    if live.get_backend_name() != "postgresql":
        raise SystemExit(
            "restore is for PostgreSQL. For SQLite, copy database.sqlite over the file."
        )
    if args.into_db == live.database and not args.replace_live:
        raise SystemExit(
            f"'{args.into_db}' is the live database. Restore into a scratch database "
            "(for example --into-db apex_test), or add --replace-live if you really mean it."
        )
    folder = Path(args.backup)
    folder = folder if folder.is_absolute() else ROOT / folder
    if not (folder / "manifest.json").is_file():
        raise SystemExit(f"{folder} is not a backup folder (no manifest.json).")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest["database_kind"] != "postgresql":
        raise SystemExit("That backup is not a PostgreSQL backup.")

    dump = folder / "database.dump"
    for name, info in manifest[
        "files"
    ].items():  # refuse a damaged backup before restoring anything
        if not (folder / name).is_file() or _sha256(folder / name) != info["sha256"]:
            raise SystemExit(
                f"{name} is missing or does not match its checksum; backup is damaged."
            )

    flags, env = _pg_args(live, args.into_db)
    _run(
        [_tool("pg_restore"), *flags, "--clean", "--if-exists", "--no-owner", str(dump)],
        env,
    )
    restored = _table_counts(live.set(database=args.into_db))
    version = _alembic_version(live.set(database=args.into_db))

    print(f"Restored {folder.name} into database '{args.into_db}'. Row counts against the backup:")
    bad = 0
    for table, expected in manifest["table_counts"].items():
        actual = restored.get(table)
        mark = "ok" if actual == expected else "MISMATCH"
        bad += actual != expected
        print(f"  {table:<20} backup={expected:<6} restored={actual!s:<6} {mark}")
    if version != manifest["migration_version"]:
        bad += 1
        print(f"  migration: backup={manifest['migration_version']} restored={version}  MISMATCH")
    if args.files_to:
        target = Path(args.files_to)
        for name in ("uploads.zip", "vector_db.zip"):
            with zipfile.ZipFile(folder / name) as archive:
                archive.extractall(target / name.removesuffix(".zip"))
        print(f"  uploads and vector index unpacked under {target}")
    print("RESULT:", "restore verified, every table matches" if not bad else f"{bad} difference(s)")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup", help="write a new backup folder")
    b.add_argument("--out-dir", default="backups", help="where backups go (default: backups)")
    b.add_argument(
        "--keep", type=int, default=0, help="keep only the newest N backups (default: all)"
    )
    r = sub.add_parser("restore", help="restore a backup into another database and check it")
    r.add_argument("--backup", required=True, help="a backups/apex-... folder")
    r.add_argument("--into-db", required=True, help="database name on the same server")
    r.add_argument("--replace-live", action="store_true", help="allow restoring over the live one")
    r.add_argument("--files-to", help="also unpack uploads and the vector index into this folder")
    args = parser.parse_args()
    return {"backup": backup, "restore": restore}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
