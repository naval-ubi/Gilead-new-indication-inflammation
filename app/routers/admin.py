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
    client = get_connector(c.name)
    if client is None:  # no client built for this source yet: report it, do not mark the source as broken
        return {"ok": False, "detail": "No client is connected for this source yet", "status": c.health, "connected": False}
    ok, detail = client.health()
    c.health = "Healthy" if ok else "Failed"
    c.last_sync = datetime.now(timezone.utc) if ok else c.last_sync
    db.commit()
    return {"ok": ok, "detail": detail, "status": c.health, "connected": True}


# =============================================================== ADMIN
ADMIN_KINDS = {"workflow": "AI Workflows", "template": "Report Templates", "prompt": "Prompts Library"}


@router.get("/admin/overview")
def overview(db: DB, user: m.User = Depends(require("administer"))):
    """Admin overview cards. Flat counts kept for v0.1 clients; `sections` adds the 'N live / N enabled' badge."""
    users = db.scalars(select(m.User)).all()
    conns = db.scalars(select(m.Connector)).all()
    models = db.scalars(select(m.ScoringModel)).all()
    counts = {"users": len(users), "connectors": len(conns), "scoring": len(models)}
    sections = [
        {"key": "users", "name": "Users & Groups", "count": len(users), "badge": f"{sum(u.status == 'Active' for u in users)} live",
         "groups": len(db.scalars(select(m.Group)).all()), "roles": len(db.scalars(select(m.Role)).all())},
        {"key": "scoring", "name": "Scoring Models", "count": len(models),
         "badge": f"{sum(x.status == 'Published' for x in models)} live"},
        {"key": "connectors", "name": "Data Connectors", "count": len(conns), "badge": f"{sum(c.enabled for c in conns)} enabled"},
    ]
    for k, name in ADMIN_KINDS.items():
        rows = db.scalars(select(m.AdminRecord).where(m.AdminRecord.kind == k)).all()
        counts[k] = len(rows)
        sections.append({"key": k, "name": name, "count": len(rows), "badge": f"{sum(r.status == 'Published' for r in rows)} live"})
    order = ["users", "workflow", "scoring", "template", "connectors", "prompt"]  # same order as the Admin tabs
    sections.sort(key=lambda x: order.index(x["key"]))
    return {**counts, "sections": sections}


# ---- users
class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=200)
    role: str = "Analyst"           # a name from GET /admin/roles
    groups: list[str] = []
    status: Literal["Active", "Inactive"] = "Active"
    password: str | None = None


def u_out(u: m.User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role.name, "role_id": u.role_id,
            "groups": u.group_names, "group_ids": [g.id for g in u.groups], "status": u.status, "last_login": u.last_login}


def _role(db, name: str) -> m.Role:
    r = db.scalar(select(m.Role).where(m.Role.name == name))
    if not r:
        raise HTTPException(422, f"Unknown role: {name}")
    return r


def _groups(db, names: list[str]) -> list[m.Group]:
    """Resolve group names; unknown names are created so the existing 'type a group' admin flow keeps working."""
    out = []
    for n in dict.fromkeys(x.strip() for x in names if x.strip()):
        g = db.scalar(select(m.Group).where(m.Group.name == n))
        if g is None:
            g = m.Group(name=n)
            db.add(g)
        out.append(g)
    return out


@router.get("/admin/users")
def users(db: DB, user: m.User = Depends(require("administer"))):
    return [u_out(u) for u in db.scalars(select(m.User).order_by(m.User.name))]


@router.post("/admin/users", status_code=201)
def create_user(body: UserIn, db: DB, user: m.User = Depends(require("administer"))):
    if db.scalar(select(m.User).where(m.User.email == body.email.lower())):
        raise HTTPException(409, "Email already exists")
    u = m.User(name=body.name, email=body.email.lower(), role=_role(db, body.role), groups=_groups(db, body.groups),
               status=body.status,
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
    u.name, u.role, u.groups, u.status = body.name, _role(db, body.role), _groups(db, body.groups), body.status
    if body.password:
        u.password_hash = hash_password(body.password)
    db.commit()
    audit(db, user, f"Edited user {u.email} (change recorded with previous and new values)", "user", u.id, before, u_out(u))
    return u_out(u)


# ---- roles & groups (normalised tables)
@router.get("/admin/roles")
def roles(db: DB, user: m.User = Depends(require("administer"))):
    """Roles with the permissions each grants, and the full permission list (for a role editor)."""
    counts = {}
    for u in db.scalars(select(m.User)):
        counts[u.role_id] = counts.get(u.role_id, 0) + 1
    return {"roles": [{"id": r.id, "name": r.name, "description": r.description, "user_count": counts.get(r.id, 0),
                       "permissions": [p.code for p in r.permissions]} for r in db.scalars(select(m.Role).order_by(m.Role.id))],
            "permissions": [{"id": p.id, "code": p.code, "description": p.description}
                            for p in db.scalars(select(m.Permission).order_by(m.Permission.id))]}


class RolePermsIn(BaseModel):
    permissions: list[str]


@router.put("/admin/roles/{rid}/permissions")
def set_role_permissions(rid: int, body: RolePermsIn, db: DB, user: m.User = Depends(require("administer"))):
    r = db.get(m.Role, rid)
    if not r:
        raise HTTPException(404, "Role not found")
    perms = {p.code: p for p in db.scalars(select(m.Permission))}
    unknown = [c for c in body.permissions if c not in perms]
    if unknown:
        raise HTTPException(422, f"Unknown permissions: {unknown}")
    if r.id == user.role_id and "administer" not in body.permissions:
        raise HTTPException(409, "You cannot remove 'administer' from your own role")
    before = {"permissions": [p.code for p in r.permissions]}
    r.permissions = [perms[c] for c in dict.fromkeys(body.permissions)]
    db.commit()
    audit(db, user, f"Changed permissions of role {r.name}", "role", r.id, before, {"permissions": body.permissions})
    return {"id": r.id, "name": r.name, "permissions": [p.code for p in r.permissions]}


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=200)


def g_out(g: m.Group, members: int) -> dict:
    return {"id": g.id, "name": g.name, "description": g.description, "member_count": members}


@router.get("/admin/groups")
def list_groups(db: DB, user: m.User = Depends(require("administer"))):
    counts = {}
    for row in db.scalars(select(m.UserGroup)):
        counts[row.group_id] = counts.get(row.group_id, 0) + 1
    return [g_out(g, counts.get(g.id, 0)) for g in db.scalars(select(m.Group).order_by(m.Group.name))]


@router.post("/admin/groups", status_code=201)
def create_group(body: GroupIn, db: DB, user: m.User = Depends(require("administer"))):
    if db.scalar(select(m.Group).where(m.Group.name == body.name.strip())):
        raise HTTPException(409, "A group with this name already exists")
    g = m.Group(name=body.name.strip(), description=body.description)
    db.add(g)
    db.commit()
    audit(db, user, f"Created group {g.name}", "group", g.id)
    return g_out(g, 0)


@router.patch("/admin/groups/{gid}")
def update_group(gid: int, body: GroupIn, db: DB, user: m.User = Depends(require("administer"))):
    g = db.get(m.Group, gid)
    if not g:
        raise HTTPException(404, "Group not found")
    clash = db.scalar(select(m.Group).where(m.Group.name == body.name.strip(), m.Group.id != gid))
    if clash:
        raise HTTPException(409, "A group with this name already exists")
    before = {"name": g.name, "description": g.description}
    g.name, g.description = body.name.strip(), body.description
    db.commit()
    audit(db, user, f"Edited group {g.name}", "group", g.id, before, {"name": g.name, "description": g.description})
    return g_out(g, len(db.scalars(select(m.UserGroup).where(m.UserGroup.group_id == gid)).all()))


@router.delete("/admin/groups/{gid}", status_code=204)
def delete_group(gid: int, db: DB, user: m.User = Depends(require("administer"))):
    """Only empty groups that nothing is shared with can be deleted, so no access is lost silently."""
    g = db.get(m.Group, gid)
    if not g:
        raise HTTPException(404, "Group not found")
    if db.scalar(select(m.UserGroup).where(m.UserGroup.group_id == gid).limit(1)):
        raise HTTPException(409, "Remove the group's members first")
    if db.scalar(select(m.ItemShare).where(m.ItemShare.group_id == gid).limit(1)):
        raise HTTPException(409, "Items are still shared with this group")
    audit(db, user, f"Deleted group {g.name}", "group", g.id)
    db.delete(g)
    db.commit()


# ---- generic config records (workflows / templates / prompts)
class RecordIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    status: Literal["Draft", "Published", "Inactive"] = "Draft"
    version: str = Field(default="v1", max_length=20)
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
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(default="v1", max_length=20)
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
