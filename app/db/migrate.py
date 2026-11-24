"""PostgreSQL start-up: apply Alembic migrations, then make sure reference data (roles, permissions, indications)
exists. SQLite development databases keep using app.db.upgrade.upgrade_schema.

Several app instances may start at the same time (e.g. two containers); a PostgreSQL advisory lock makes them
migrate one after the other instead of racing.
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
MIGRATION_LOCK = 724_501  # any constant; identifies this app's migration lock


def alembic_config(connection=None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["configure_logger"] = False  # keep the app's logging configuration
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def migrate_postgres(engine) -> None:
    from app.db.upgrade import ensure_reference_data

    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK})
        try:
            insp = inspect(conn)
            if insp.has_table("users") and not insp.has_table("alembic_version"):
                raise RuntimeError(
                    "This PostgreSQL database already has tables but no migration history. If its schema matches the "
                    "current models, run 'alembic stamp head' once, then start the app again.")
            conn.commit()
            command.upgrade(alembic_config(conn), "head")
            conn.commit()
            db = Session(bind=conn)
            ensure_reference_data(db)
            conn.commit()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK})
            conn.commit()


def prepare_database(engine) -> None:
    """Called on start-up: the right schema routine for the configured database."""
    if engine.dialect.name == "postgresql":
        migrate_postgres(engine)
    else:
        from app.db.upgrade import upgrade_schema
        upgrade_schema(engine)
