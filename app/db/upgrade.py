"""Schema upgrade run on every start-up. Idempotent: each step checks the current schema before doing anything.

v0.4 normalisation (converts databases created by earlier versions, keeping their data):
  users.role (text)          -> users.role_id -> roles (+ permissions, role_permissions)
  users.groups (JSON list)   -> groups + user_groups
  item_shares.group (text)   -> item_shares.group_id -> groups
  pipeline_assets            -> sponsors, modalities, targets, indications, drug_assets, pipeline_programs
  assessments.overrides JSON -> assessment_overrides

This keeps local and shared dev databases working without manual steps. For staging / production use Alembic
migrations generated from app.db.models (see docs/Database_ER_Guide.pdf, section 8.8).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.db import models as m
from app.db.base import Base, add_missing_columns
from app.services import catalog

log = logging.getLogger(__name__)


def _cols(conn, table: str) -> set[str]:
    insp = inspect(conn)
    return {c["name"] for c in insp.get_columns(table)} if insp.has_table(table) else set()


def _json(v):
    return json.loads(v) if isinstance(v, str) else (v or None)


def _get_or_create(db: Session, model, **key):
    obj = db.scalar(select(model).filter_by(**key))
    if obj is None:
        obj = model(**key)
        db.add(obj)
        db.flush()
    return obj


# ------------------------------------------------------------------ reference data (always present, also in production)
def ensure_reference_data(db: Session) -> None:
    perms = {p: _get_or_create(db, m.Permission, code=p) for p in catalog.PERMISSIONS}
    for code, desc in catalog.PERMISSIONS.items():
        perms[code].description = perms[code].description or desc
    for name, (desc, codes) in catalog.ROLES.items():
        role = db.scalar(select(m.Role).filter_by(name=name))
        if role is None:  # only new roles get the default permission set; admin edits are never overwritten
            role = m.Role(name=name, description=desc, permissions=[perms[c] for c in codes])
            db.add(role)
    for code in catalog.CORE_INDICATIONS + catalog.ADDITIONAL_INDICATIONS:
        ind = _get_or_create(db, m.Indication, code=code)
        ind.name = ind.name or catalog.INDICATION_FULL_NAMES.get(code, "")
    db.commit()


# ------------------------------------------------------------------ legacy conversions
def _upgrade_users(conn, db: Session) -> None:
    cols = _cols(conn, "users")
    if "role" not in cols and "groups" not in cols:
        return
    log.info("upgrade: users.role/groups -> roles/groups tables")
    if "role_id" not in cols:
        conn.execute(text("ALTER TABLE users ADD COLUMN role_id INTEGER REFERENCES roles(id)"))
    roles = {r.name: r.id for r in db.scalars(select(m.Role))}
    fallback = roles[catalog.DEFAULT_ROLE]
    rows = conn.execute(text("SELECT id, role, groups FROM users")).all() if "groups" in cols else \
        [(r[0], r[1], None) for r in conn.execute(text("SELECT id, role FROM users"))]
    for uid, role, groups in rows:
        conn.execute(text("UPDATE users SET role_id = :r WHERE id = :u"), {"r": roles.get(role, fallback), "u": uid})
        for g in _json(groups) or []:
            gid = _get_or_create(db, m.Group, name=g).id
            conn.execute(text("INSERT INTO user_groups (user_id, group_id) SELECT :u, :g WHERE NOT EXISTS "
                              "(SELECT 1 FROM user_groups WHERE user_id = :u AND group_id = :g)"), {"u": uid, "g": gid})
    for col in ("role", "groups"):
        if col in cols:
            conn.execute(text(f"ALTER TABLE users DROP COLUMN {col}"))


def _upgrade_shares(conn, db: Session) -> None:
    cols = _cols(conn, "item_shares")
    if "group" not in cols:
        return
    log.info("upgrade: item_shares.group -> item_shares.group_id")
    if "group_id" not in cols:
        conn.execute(text("ALTER TABLE item_shares ADD COLUMN group_id INTEGER REFERENCES groups(id)"))
    for sid, g in conn.execute(text('SELECT id, "group" FROM item_shares WHERE "group" IS NOT NULL')).all():
        gid = _get_or_create(db, m.Group, name=g).id
        conn.execute(text("UPDATE item_shares SET group_id = :g WHERE id = :s"), {"g": gid, "s": sid})
    conn.execute(text('ALTER TABLE item_shares DROP COLUMN "group"'))


def _upgrade_pipeline(conn, db: Session) -> None:
    if not inspect(conn).has_table("pipeline_assets"):
        return
    log.info("upgrade: pipeline_assets -> drug_assets + pipeline_programs + lookups")
    rows = conn.execute(text("SELECT name, sponsor, modality, target, indication, stage, activity, geography, source "
                             "FROM pipeline_assets ORDER BY id")).all()
    for name, sponsor, modality, target, indication, stage, activity, geography, source in rows:
        sp = _get_or_create(db, m.Sponsor, name=sponsor)
        asset = db.scalar(select(m.DrugAsset).filter_by(name=name, sponsor_id=sp.id))
        if asset is None:
            asset = m.DrugAsset(name=name, sponsor_id=sp.id, modality_id=_get_or_create(db, m.Modality, name=modality).id,
                                target_id=_get_or_create(db, m.Target, name=target).id)
            db.add(asset)
            db.flush()
        ind = _get_or_create(db, m.Indication, code=indication)
        if not db.scalar(select(m.PipelineProgram).filter_by(asset_id=asset.id, indication_id=ind.id, geography=geography)):
            db.add(m.PipelineProgram(asset_id=asset.id, indication_id=ind.id, stage=stage, activity=activity,
                                     geography=geography, source=source))
    db.flush()
    conn.execute(text("DROP TABLE pipeline_assets"))


def _upgrade_overrides(conn, db: Session) -> None:
    if "overrides" not in _cols(conn, "assessments"):
        return
    log.info("upgrade: assessments.overrides -> assessment_overrides")
    for aid, owner, ov in conn.execute(text("SELECT id, owner_id, overrides FROM assessments")).all():
        for ind, cells in (_json(ov) or {}).items():
            for key, o in cells.items():
                at = o.get("at")
                db.add(m.AssessmentOverride(
                    assessment_id=aid, indication=ind, criterion_key=key, score=o.get("score"),
                    justification=o.get("justification") or "", user_id=o.get("user_id") or owner,
                    created_at=datetime.fromisoformat(at) if at else datetime.now(timezone.utc)))
    db.flush()
    conn.execute(text("ALTER TABLE assessments DROP COLUMN overrides"))


def upgrade_schema(engine) -> None:
    Base.metadata.create_all(engine)  # creates tables that do not exist yet; never alters existing ones
    with engine.connect() as conn:
        db = Session(bind=conn)
        try:
            ensure_reference_data(db)
            conn.commit()  # a Session bound to a Connection joins its transaction; commit the connection itself
            for step in (_upgrade_users, _upgrade_shares, _upgrade_pipeline, _upgrade_overrides):
                step(conn, db)
                db.flush()
                conn.commit()  # each conversion is applied atomically
        except Exception:
            conn.rollback()
            raise
        finally:
            db.close()
    added = add_missing_columns(engine)  # later nullable/defaulted columns and missing indexes
    if added:
        log.info("upgrade: added %s", added)
