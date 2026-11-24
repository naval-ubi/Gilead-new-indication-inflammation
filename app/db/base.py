from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def add_missing_columns(bind=None) -> list[str]:
    """Dev convenience: create_all() never alters existing tables, so add columns that were introduced after a
    database was created, plus any declared index that is missing. Only nullable or defaulted scalar columns are
    added. Use Alembic in production."""
    bind = bind or engine
    insp = inspect(bind)
    added = []
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                if default is None and not col.nullable:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(bind.dialect)}"
                if default is not None:
                    if isinstance(default, bool):  # PostgreSQL needs TRUE/FALSE; SQLite stores 1/0
                        lit = ("TRUE" if default else "FALSE") if bind.dialect.name == "postgresql" else str(int(default))
                    elif isinstance(default, str):
                        lit = "'" + default.replace("'", "''") + "'"
                    else:
                        lit = repr(default)
                    ddl += f" DEFAULT {lit}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
            existing = {i["name"] for i in inspect(conn).get_indexes(table.name)}
            for idx in table.indexes:
                if idx.name not in existing and all(c.name in have or f"{table.name}.{c.name}" in added for c in idx.columns):
                    idx.create(conn)
                    added.append(f"index {idx.name}")
    return added
