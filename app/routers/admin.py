"""Data Source page (connectors) and Admin console (users, workflows, scoring models, templates, prompts, audit)."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import DB, CurrentUser, audit, require
from app.core.security import hash_password
from app.db import models as m
from app.services.integrations import CATEGORIES, get_connector

router = APIRouter(tags=["admin"])


# =============================================================== DATA SOURCES
def conn_out(c: m.Connector) -> dict:
    return {"id": c.id, "name": c.name, "category": c.category, "category_name": CATEGORIES[c.category],
            "description": c.description, "access": c.license, "enabled": c.enabled,
            "status": c.health if c.enabled else "Disabled", "last_sync": c.last_sync}


@router.get("/datasources")
def list_sources(db: DB, user: CurrentUser, category: int | None = None, q: str | None = None):
    rows = [c for c in db.scalars(select(m.Connector).order_by(m.Connector.category, m.Connector.name))
            if (category is None or c.category == category) and (not q or q.lower() in (c.name + c.description).lower())]
    allc = db.scalars(select(m.Connector)).all()
    return {"categories": CATEGORIES,
            "kpis": {"connectors": len(allc), "enabled": sum(c.enabled for c in allc),
                     "healthy": sum(c.enabled and c.health == "Healthy" for c in allc),
                     "needs_attention": sum(c.enabled and c.health != "Healthy" for c in allc)},
            "items": [conn_out(c) for c in rows]}


class ConnectorIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: int = Field(ge=0, le=len(CATEGORIES) - 1)
    description: str = ""
    license: Literal["Open", "Licensed"] = "Open"
    config: dict = {}


@router.post("/datasources", status_code=201)
def add_source(body: ConnectorIn, db: DB, user: m.User = Depends(require("administer"))):
    if db.scalar(select(m.Connector).where(m.Connector.name == body.name)):
        raise HTTPException(409, "A connector with this name already exists")
    c = m.Connector(**body.model_dump(), enabled=False)  # new connectors start disabled until tested
    db.add(c)
    db.commit()
    audit(db, user, f"Added connector {c.name}", "connector", c.id)
    return conn_out(c)


@router.patch("/datasources/{cid}")
def edit_source(cid: int, body: ConnectorIn, db: DB, user: m.User = Depends(require("administer"))):
    c = db.get(m.Connector, cid)
    if not c:
        raise HTTPException(404, "Not found")
    before = conn_out(c)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    db.commit()
    audit(db, user, f"Edited connector {c.name}", "connector", c.id, before, conn_out(c))
    return conn_out(c)


@router.post("/datasources/{cid}/toggle")
def toggle(cid: int, enabled: bool, db: DB, user: m.User = Depends(require("administer"))):
    """Disabling blocks NEW queries only; existing research records and provenance are kept."""
    c = db.get(m.Connector, cid)
    if not c:
        raise HTTPException(404, "Not found")
    c.enabled = enabled
    db.commit()
    audit(db, user, f"{'Enabled' if enabled else 'Disabled'} connector {c.name}", "connector", c.id)
    return conn_out(c)


@router.post("/datasources/{cid}/test")
def test(cid: int, db: DB, user: m.User = Depends(require("administer"))):
    c = db.get(m.Connector, cid)
    if not c:
        raise HTTPException(404, "Not found")
    ok, detail = get_connector(c.name).health()
    c.health = "Healthy" if ok else "Failed"
    c.last_sync = datetime.now(timezone.utc) if ok else c.last_sync
    db.commit()
    return {"ok": ok, "detail": detail, "status": c.health}


# =============================================================== ADMIN
ADMIN_KINDS = {"workflow": "AI Workflows", "template": "Report Templates", "prompt": "Prompts Library"}


@router.get("/admin/overview")
def overview(db: DB, user: m.User = Depends(require("administer"))):
    counts = {"users": len(db.scalars(select(m.User)).all()), "connectors": len(db.scalars(select(m.Connector)).all()),
              "scoring": len(db.scalars(select(m.ScoringModel)).all())}
    for k in ADMIN_KINDS:
        counts[k] = len(db.scalars(select(m.AdminRecord).where(m.AdminRecord.kind == k)).all())
    return counts


# ---- users
class UserIn(BaseModel):
    name: str
    email: str
    role: Literal["Admin", "Analyst", "Reviewer", "Viewer"] = "Analyst"
    groups: list[str] = []
    status: Literal["Active", "Inactive"] = "Active"
    password: str | None = None


def u_out(u: m.User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "groups": u.groups, "status": u.status,
            "last_login": u.last_login}


@router.get("/admin/users")
def users(db: DB, user: m.User = Depends(require("administer"))):
    return [u_out(u) for u in db.scalars(select(m.User).order_by(m.User.name))]


@router.post("/admin/users", status_code=201)
def create_user(body: UserIn, db: DB, user: m.User = Depends(require("administer"))):
    if db.scalar(select(m.User).where(m.User.email == body.email.lower())):
        raise HTTPException(409, "Email already exists")
    u = m.User(name=body.name, email=body.email.lower(), role=body.role, groups=body.groups, status=body.status,
               password_hash=hash_password(body.password) if body.password else "")  # SSO users have no local password
    db.add(u)
    db.commit()
    audit(db, user, f"Created user {u.email}", "user", u.id)
    return u_out(u)


@router.patch("/admin/users/{uid}")
def update_user(uid: int, body: UserIn, db: DB, user: m.User = Depends(require("administer"))):
    u = db.get(m.User, uid)
    if not u:
        raise HTTPException(404, "Not found")
    before = u_out(u)
    u.name, u.role, u.groups, u.status = body.name, body.role, body.groups, body.status
    if body.password:
        u.password_hash = hash_password(body.password)
    db.commit()
    audit(db, user, f"Edited user {u.email} (change recorded with previous and new values)", "user", u.id, before, u_out(u))
    return u_out(u)


# ---- generic config records (workflows / templates / prompts)
class RecordIn(BaseModel):
    name: str
    status: Literal["Draft", "Published", "Inactive"] = "Draft"
    version: str = "v1"
    data: dict = {}


def r_out(r: m.AdminRecord) -> dict:
    return {"id": r.id, "kind": r.kind, "name": r.name, "status": r.status, "version": r.version, "data": r.data,
            "updated_at": r.updated_at}


@router.get("/admin/records/{kind}")
def records(kind: Literal["workflow", "template", "prompt"], db: DB, q: str | None = None,
            user: m.User = Depends(require("administer"))):
    rows = db.scalars(select(m.AdminRecord).where(m.AdminRecord.kind == kind).order_by(m.AdminRecord.id.desc())).all()
    return [r_out(r) for r in rows if not q or q.lower() in r.name.lower()]


@router.post("/admin/records/{kind}", status_code=201)
def create_record(kind: Literal["workflow", "template", "prompt"], body: RecordIn, db: DB,
                  user: m.User = Depends(require("administer"))):
    r = m.AdminRecord(kind=kind, **body.model_dump())
    db.add(r)
    db.commit()
    audit(db, user, f"Created {kind} '{r.name}'", kind, r.id)
    return r_out(r)


@router.patch("/admin/records/{kind}/{rid}")
def update_record(kind: str, rid: int, body: RecordIn, db: DB, user: m.User = Depends(require("administer"))):
    r = db.get(m.AdminRecord, rid)
    if not r or r.kind != kind:
        raise HTTPException(404, "Not found")
    before = r_out(r)
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    db.commit()
    audit(db, user, f"Edited {kind} '{r.name}'", kind, r.id, before, r_out(r))
    return r_out(r)


@router.post("/admin/records/{kind}/{rid}/deactivate")
def deactivate(kind: str, rid: int, db: DB, user: m.User = Depends(require("administer"))):
    """Historical results stay intact; items referencing this record can show a warning."""
    r = db.get(m.AdminRecord, rid)
    if not r or r.kind != kind:
        raise HTTPException(404, "Not found")
    r.status = "Inactive"
    db.commit()
    audit(db, user, f"Deactivated {kind} '{r.name}'", kind, r.id)
    return r_out(r)


# ---- scoring models (admin-managed templates consumed by /scoring)
class ModelIn(BaseModel):
    name: str
    version: str = "v1"
    status: Literal["Draft", "Published", "Inactive"] = "Draft"
    criteria: list[dict]


@router.get("/admin/scoring-models")
def scoring_models(db: DB, user: m.User = Depends(require("administer"))):
    return [{"id": s.id, "name": s.name, "version": s.version, "status": s.status, "criteria": s.criteria}
            for s in db.scalars(select(m.ScoringModel))]


@router.post("/admin/scoring-models", status_code=201)
def create_scoring_model(body: ModelIn, db: DB, user: m.User = Depends(require("administer"))):
    tot = sum(c.get("weight", 0) for c in body.criteria)
    if body.status == "Published" and abs(tot - 100) > 0.01:
        raise HTTPException(422, f"Default weights must total 100% to publish (currently {tot:g}%)")
    s = m.ScoringModel(**body.model_dump())
    db.add(s)
    db.commit()
    audit(db, user, f"Created scoring model '{s.name}'", "scoring_model", s.id)
    return {"id": s.id}


# ---- audit log
@router.get("/admin/audit")
def audit_log(db: DB, user: m.User = Depends(require("administer")), limit: int = 100):
    rows = db.scalars(select(m.AuditLog).order_by(m.AuditLog.id.desc()).limit(limit)).all()
    names = {u.id: u.name for u in db.scalars(select(m.User))}
    return [{"id": a.id, "user": names.get(a.user_id, "system"), "action": a.action, "entity": a.entity,
             "entity_id": a.entity_id, "before": a.before, "after": a.after, "at": a.created_at} for a in rows]
