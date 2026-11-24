"""Alembic environment. The database address comes from the app settings (DATABASE_URL), never from alembic.ini.

Common commands (from the repo root):
    alembic upgrade head                         apply all migrations
    alembic revision --autogenerate -m "msg"     create a migration from changes in app/db/models.py
    alembic current / alembic history            show the applied revision / all revisions
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.db.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import settings
from app.db.base import Base
from app.db.models import UTCDateTime

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Write the app's UTC timestamp type as a plain timezone-aware DateTime, so migrations do not import app code."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of applying it:  alembic upgrade head --sql"""
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, compare_type=True, render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")  # passed in when migrations run from app start-up
    if connection is None:
        engine = engine_from_config({"sqlalchemy.url": database_url()}, prefix="sqlalchemy.", poolclass=pool.NullPool)
        with engine.connect() as conn:
            _run(conn)
    else:
        _run(connection)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
