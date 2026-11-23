"""Upgrading a v0.3 database (role/groups as text/JSON, free-text share groups, pipeline_assets, overrides JSON)
to the normalised v0.4 schema must keep every piece of data. Uses the real v0.3 schema exported from SQLite."""
import json
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.db import models as m
from app.db.upgrade import upgrade_schema

SCHEMA_V03 = (Path(__file__).parent / "fixtures" / "schema_v03.sql").read_text()
NOW = "2026-11-20 10:00:00"


def legacy_db(path) -> object:
    eng = create_engine(f"sqlite:///{path}")
    with eng.begin() as c:
        for stmt in filter(str.strip, SCHEMA_V03.split(";")):
            c.execute(text(stmt))
        ctx, prefs = json.dumps({"target": "IRAK4", "indications": ["SLE"]}), json.dumps({})
        c.execute(text("INSERT INTO users (id,name,email,password_hash,role,groups,status,context,prefs) VALUES "
                       "(1,'Ann','ann@x.com','', 'Admin', :g1, 'Active', :c, :p),"
                       "(2,'Bob','bob@x.com','', 'Reviewer', :g2, 'Active', :c, :p),"
                       "(3,'Cy','cy@x.com','', 'Wizard', '[]', 'Active', :c, :p)"),
                  {"g1": json.dumps(["R&D", "Clinical"]), "g2": json.dumps(["Clinical"]), "c": ctx, "p": prefs})
        c.execute(text("INSERT INTO workspace_items (id,owner_id,title,item_type,module,folder,status,question,summary,params,"
                       "source_count,archived,created_at,updated_at) VALUES (1,1,'Item','Search','ai','','Saved','q','',"
                       "'{}',0,0,:t,:t)"), {"t": NOW})
        c.execute(text("INSERT INTO item_shares (id,item_id,user_id,\"group\",permission) VALUES "
                       "(1,1,NULL,'Clinical','edit'),(2,1,2,NULL,'view'),(3,1,NULL,'Brand New Team','view')"))
        c.execute(text("INSERT INTO pipeline_assets (id,name,sponsor,modality,target,indication,stage,activity,geography,"
                       "source,updated_at) VALUES "
                       "(1,'Asset A','Sponsor 1','Small molecule','IRAK4 inhibitor','SLE','Phase 2','Recruiting','US','X',:t),"
                       "(2,'Asset A','Sponsor 1','Small molecule','IRAK4 inhibitor','RA','Phase 1','Active','US','X',:t),"
                       "(3,'Asset F','Sponsor 6','Small molecule','IRAK4/IRAK1 dual inhibitor','SLE','Research','—','US','X',:t)"),
                  {"t": NOW})
        c.execute(text("INSERT INTO scoring_models (id,name,version,status,criteria) VALUES (1,'M','v1','Published','[]')"))
        ov = {"RA": {"population": {"score": 4, "justification": "Registry data", "user_id": 2, "at": "2026-11-19T09:00:00+00:00"}}}
        c.execute(text("INSERT INTO assessments (id,owner_id,model_id,title,target,indications,criteria,weights,ai_scores,"
                       "overrides,status,created_at,updated_at) VALUES (1,1,1,'A','IRAK4','[\"RA\"]','[]','{}','{}',:o,"
                       "'Draft',:t,:t)"), {"o": json.dumps(ov), "t": NOW})
    return eng


def test_upgrade_v03_keeps_all_data(tmp_path):
    eng = legacy_db(tmp_path / "v03.db")
    upgrade_schema(eng)
    upgrade_schema(eng)  # idempotent

    insp = inspect(eng)
    assert "role" not in {c["name"] for c in insp.get_columns("users")}
    assert "group" not in {c["name"] for c in insp.get_columns("item_shares")}
    assert "overrides" not in {c["name"] for c in insp.get_columns("assessments")}
    assert not insp.has_table("pipeline_assets")

    with Session(eng) as db:
        ann, bob, cy = (db.get(m.User, i) for i in (1, 2, 3))
        assert ann.role.name == "Admin" and "administer" in ann.permission_codes
        assert ann.group_names == ["Clinical", "R&D"] and bob.group_names == ["Clinical"]
        assert cy.role.name == "Analyst"  # unknown legacy role falls back to the default role

        shares = {s.id: s for s in db.scalars(select(m.ItemShare))}
        assert shares[1].group.name == "Clinical" and shares[1].permission == "edit"
        assert shares[2].user_id == 2 and shares[2].group_id is None
        assert shares[3].group.name == "Brand New Team"  # free-text group names become groups

        rows = sorted((p.as_row() for p in db.scalars(select(m.PipelineProgram)).unique()), key=lambda r: r["indication"])
        assert [(r["name"], r["indication"], r["stage"]) for r in rows] == [
            ("Asset A", "RA", "Phase 1"), ("Asset A", "SLE", "Phase 2"), ("Asset F", "SLE", "Research")]
        assert len(db.scalars(select(m.DrugAsset)).all()) == 2  # Asset A stored once, two programs

        a = db.get(m.Assessment, 1)
        assert a.overrides["RA"]["population"]["score"] == 4
        assert a.override_rows[0].user_id == 2 and a.override_rows[0].justification == "Registry data"


def test_bob_sees_item_through_group_share(tmp_path):
    from app.routers.workspace import access_of

    eng = legacy_db(tmp_path / "v03b.db")
    upgrade_schema(eng)
    with Session(eng) as db:
        item = db.get(m.WorkspaceItem, 1)
        assert access_of(item, db.get(m.User, 2)) == "edit"   # via the Clinical group share (edit beats view)
        assert access_of(item, db.get(m.User, 3)) is None
