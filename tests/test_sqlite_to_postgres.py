"""SQLite -> PostgreSQL migration. Runs only when TEST_DATABASE_URL points at a PostgreSQL test database
(its contents are dropped), e.g. TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/gilead_test pytest"""
import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text

PG = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not PG.startswith("postgresql"), reason="needs TEST_DATABASE_URL (PostgreSQL)")
ROOT = Path(__file__).resolve().parents[1]


def empty_pg():
    eng = create_engine(PG)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    return eng


def test_migrate_repo_database_and_rerun_safety(tmp_path):
    from app.db import models as m
    from app.db.sqlite_to_postgres import main

    src = tmp_path / "source.db"
    shutil.copy(ROOT / "gilead.db", src)
    before = src.read_bytes()
    eng = empty_pg()

    assert main(["--source", f"sqlite:///{src}", "--target", PG, "--check"]) == 0
    with eng.connect() as c:                                   # --check writes nothing
        assert c.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")) == 0
    assert main(["--source", f"sqlite:///{src}", "--target", PG]) == 0
    assert src.read_bytes() == before                          # the source file is never modified
    assert main(["--source", f"sqlite:///{src}", "--target", PG]) == 1      # target has data: refused
    assert main(["--source", f"sqlite:///{src}", "--target", PG, "--replace"]) == 0

    with eng.begin() as c:                                     # sequences continue after the copied ids
        max_id = c.scalar(select(func.max(m.Group.id)))
        new_id = c.execute(m.Group.__table__.insert().values(name="seq-check", description="")
                           .returning(m.Group.id)).scalar()
        assert new_id == max_id + 1


def test_too_long_text_is_reported_before_writing(tmp_path):
    from app.db.sqlite_to_postgres import main

    src = tmp_path / "long.db"
    shutil.copy(ROOT / "gilead.db", src)
    sq = create_engine(f"sqlite:///{src}")
    with sq.begin() as c:                                      # SQLite accepts this; PostgreSQL would not
        c.execute(text("UPDATE groups SET name = :n WHERE id = (SELECT MIN(id) FROM groups)"), {"n": "x" * 120})
    empty_pg()
    assert main(["--source", f"sqlite:///{src}", "--target", PG]) == 1
