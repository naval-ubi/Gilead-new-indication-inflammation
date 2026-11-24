"""Copy a SQLite database (e.g. gilead.db) into PostgreSQL.

    python -m app.db.sqlite_to_postgres --target postgresql+psycopg://USER:PASSWORD@HOST:5432/gilead
    python -m app.db.sqlite_to_postgres --target ... --check          # checks only, writes nothing
    python -m app.db.sqlite_to_postgres --source sqlite:///./other.db --target ... --replace

What it does
  1. Works on a temporary copy of the SQLite file (the original is never modified) and brings that copy to the
     current schema first, so databases from older versions migrate too.
  2. Checks the data fits PostgreSQL: SQLite ignores text-length limits, PostgreSQL enforces them.
  3. Applies the Alembic migrations to the target. The target must be empty unless --replace is given.
  4. Copies every table in dependency order (parents before children) in ONE transaction: any error leaves the
     target as it was.
  5. Resets the id sequences so new rows continue after the copied ids.
  6. Verifies: row counts and every row's values must match between source and target.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from sqlalchemy import Integer, String, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url

import app.db.models  # noqa: F401  (registers every table)
from app.db.base import Base

BATCH = 500


def log(msg: str) -> None:
    print(msg, flush=True)


def prepare_source(source_url: str):
    """Upgrade a temporary copy of the SQLite file to the current schema and return an engine on it."""
    from app.db.upgrade import upgrade_schema

    path = make_url(source_url).database
    if not path or not Path(path).exists():
        sys.exit(f"Source SQLite file not found: {path}")
    copy = Path(tempfile.mkdtemp()) / "source_copy.db"
    shutil.copy(path, copy)
    eng = create_engine(f"sqlite:///{copy}")
    upgrade_schema(eng)
    return eng


def too_long(src) -> list[str]:
    """Text values longer than their column allows (PostgreSQL would reject them)."""
    problems = []
    with src.connect() as c:
        for t in Base.metadata.sorted_tables:
            for col in t.columns:
                if isinstance(col.type, String) and col.type.length:
                    n = c.scalar(select(func.count()).select_from(t).where(func.length(col) > col.type.length))
                    if n:
                        problems.append(f"{t.name}.{col.name}: {n} value(s) longer than {col.type.length} characters")
    return problems


def target_rows(conn) -> dict[str, int]:
    insp = inspect(conn)
    return {t.name: conn.scalar(select(func.count()).select_from(t))
            for t in Base.metadata.sorted_tables if insp.has_table(t.name)}


def migrate_target_schema(tgt) -> None:
    from alembic import command

    from app.db.migrate import alembic_config

    with tgt.begin() as conn:
        command.upgrade(alembic_config(conn), "head")


def copy_all(src, tgt, replace: bool) -> dict[str, int]:
    copied = {}
    tables = Base.metadata.sorted_tables
    with src.connect() as s, tgt.begin() as t:          # one transaction on the target: all or nothing
        if replace:
            t.execute(text("TRUNCATE " + ", ".join(f'"{x.name}"' for x in tables) + " RESTART IDENTITY CASCADE"))
        for table in tables:
            order = list(table.primary_key.columns) or list(table.columns)[:1]
            result = s.execute(select(table).order_by(*order))
            total = 0
            while rows := result.fetchmany(BATCH):
                t.execute(table.insert(), [dict(r._mapping) for r in rows])
                total += len(rows)
            copied[table.name] = total
            log(f"  {table.name:<22} {total:>6} rows")
        for table in tables:                              # new rows must continue after the copied ids
            pk = list(table.primary_key.columns)
            if len(pk) == 1 and isinstance(pk[0].type, Integer) and pk[0].autoincrement in (True, "auto"):
                t.execute(text(f"SELECT setval(pg_get_serial_sequence('{table.name}', '{pk[0].name}'), "
                               f"COALESCE((SELECT MAX({pk[0].name}) FROM \"{table.name}\"), 1), "
                               f"(SELECT MAX({pk[0].name}) FROM \"{table.name}\") IS NOT NULL)"))
    return copied


def verify(src, tgt) -> list[str]:
    """Every row of every table must be identical (values compared after type conversion)."""
    issues = []
    with src.connect() as s, tgt.connect() as t:
        for table in Base.metadata.sorted_tables:
            order = list(table.primary_key.columns) or list(table.columns)
            a = [dict(r._mapping) for r in s.execute(select(table).order_by(*order))]
            b = [dict(r._mapping) for r in t.execute(select(table).order_by(*order))]
            if len(a) != len(b):
                issues.append(f"{table.name}: {len(a)} rows in SQLite, {len(b)} in PostgreSQL")
            elif a != b:
                bad = next(i for i, (x, y) in enumerate(zip(a, b)) if x != y)
                diff = [k for k in a[bad] if a[bad][k] != b[bad][k]]
                issues.append(f"{table.name}: row {bad + 1} differs in {diff}")
    return issues


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Copy a SQLite database into PostgreSQL.")
    ap.add_argument("--source", default="sqlite:///./gilead.db", help="SQLite URL (default: sqlite:///./gilead.db)")
    ap.add_argument("--target", required=True, help="PostgreSQL URL, e.g. postgresql+psycopg://user:pass@host:5432/gilead")
    ap.add_argument("--check", action="store_true", help="run the checks only; write nothing")
    ap.add_argument("--replace", action="store_true", help="empty the target tables first (DELETES target data)")
    args = ap.parse_args(argv)

    if not args.source.startswith("sqlite"):
        sys.exit("--source must be a SQLite URL")
    if make_url(args.target).get_backend_name() != "postgresql":
        sys.exit("--target must be a PostgreSQL URL")
    safe_target = make_url(args.target).render_as_string(hide_password=True)

    log(f"Source: {args.source} (working on a temporary copy)")
    src = prepare_source(args.source)
    problems = too_long(src)
    if problems:
        log("These values do not fit PostgreSQL's column limits. Shorten them in the source, then run again:")
        for p in problems:
            log(f"  - {p}")
        return 1
    log("Source data fits PostgreSQL's column limits.")

    tgt = create_engine(args.target, pool_pre_ping=True)
    with tgt.connect() as c:
        existing = {k: v for k, v in target_rows(c).items() if v}
    if existing and not args.replace:
        log(f"Target {safe_target} already has data: {existing}")
        log("Use an empty database, or add --replace to delete the target's data first.")
        return 1
    if args.check:
        log(f"Check passed. Nothing was written. Target: {safe_target}")
        return 0

    log(f"Target: {safe_target}")
    log("Applying migrations to the target ...")
    migrate_target_schema(tgt)
    log("Copying (one transaction) ...")
    copied = copy_all(src, tgt, args.replace)
    log("Verifying every row ...")
    issues = verify(src, tgt)
    if issues:
        log("Verification FAILED:")
        for i in issues:
            log(f"  - {i}")
        return 1
    log(f"Done. {sum(copied.values())} rows in {len(copied)} tables copied and verified.")
    log("Next: set DATABASE_URL to the PostgreSQL URL in .env (and SEED_SAMPLE_DATA=false for shared environments).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
