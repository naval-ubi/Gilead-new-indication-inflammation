"""Home dashboard tables, Workspace (saved items, sharing, versions, notes) and History."""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app.core.deps import DB, CurrentUser, audit, require
from app.db import models as m

router = APIRouter(tags=["workspace"])
MODULE_NAMES = {"ai": "AI Search", "pipeline": "Drug Pipeline Explorer", "cioa": "CIOA", "scoring": "Prioritization Scoring"}


def utc_now():
    return datetime.now(timezone.utc)


# ================================================================== HOME
@router.get("/home")
def home(db: DB, user: CurrentUser, tab: Literal["in_progress", "recent_results", "saved"] = "in_progress",
         module: str | None = None, sort: Literal["date", "module"] = "date", desc: bool = True,
         limit: int = Query(50, le=200)):
    rows: list[dict] = []
    if tab == "saved":
        q = select(m.WorkspaceItem).where(m.WorkspaceItem.owner_id == user.id, m.WorkspaceItem.archived.is_(False))
        for i in db.scalars(q):
            rows.append({"id": i.id, "kind": "item", "title": i.title, "module": i.module, "last_worked": i.updated_at})
    else:
        statuses = ("In Progress", "Paused", "Failed") if tab == "in_progress" else ("Completed",)
        q = select(m.Run).where(m.Run.user_id == user.id, m.Run.deleted.is_(False), m.Run.status.in_(statuses))
        for r in db.scalars(q):
            rows.append({"id": r.id, "kind": "run", "title": r.title, "module": r.module, "status": r.status,
                         "step_label": r.step_label, "last_worked": r.updated_at})
    if module and module != "All":
        rows = [r for r in rows if MODULE_NAMES.get(r["module"]) == module or r["module"] == module]
    rows.sort(key=(lambda r: r["module"]) if sort == "module" else (lambda r: r["last_worked"]), reverse=desc)
    for r in rows:
        r["module_name"] = MODULE_NAMES.get(r["module"], r["module"])
    return {"tab": tab, "items": rows[:limit]}


class RowAction(BaseModel):
    action: Literal["duplicate", "delete"]


@router.post("/home/runs/{run_id}")
def home_run_action(run_id: int, body: RowAction, db: DB, user: CurrentUser):
    r = db.get(m.Run, run_id)
    if not r or r.user_id != user.id:
        raise HTTPException(404, "Not found")
    if body.action == "delete":
        r.deleted = True
    else:
        db.add(m.Run(user_id=user.id, module=r.module, kind=r.kind, title=r.title + " (copy)", question=r.question,
                     mode=r.mode, params=r.params, steps=r.steps, status=r.status, result=r.result,
                     step_index=r.step_index, step_label=r.step_label, project=r.project))
    db.commit()
    return {"ok": True}


# ================================================================== WORKSPACE
def access_of(item: m.WorkspaceItem, user: m.User) -> str | None:
    """Owner | edit | view | None - the single source of truth for item permissions."""
    if item.owner_id == user.id:
        return "Owner"
    best = None
    for s in item.shares:
        if s.user_id == user.id or (s.group and s.group in (user.groups or [])):
            if s.permission == "edit":
                return "edit"
            best = "view"
    return best


def get_item(db, item_id: int, user: m.User, need: str = "view") -> tuple[m.WorkspaceItem, str]:
    it = db.get(m.WorkspaceItem, item_id)
    acc = access_of(it, user) if it else None
    if not acc:
        raise HTTPException(404, "Item not found")
    if need == "owner" and acc != "Owner":
        raise HTTPException(403, "Only the owner can do this")
    if need == "edit" and acc not in ("Owner", "edit"):
        raise HTTPException(403, "You have view-only access")
    return it, acc


def item_out(it: m.WorkspaceItem, acc: str, db, detail: bool = False) -> dict:
    owner = db.get(m.User, it.owner_id)
    d = {"id": it.id, "title": it.title, "type": it.item_type, "module": it.module, "module_name": MODULE_NAMES.get(it.module),
         "owner": owner.name if owner else "", "access": acc, "status": it.status, "folder": it.folder,
         "archived": it.archived, "updated_at": it.updated_at, "run_id": it.run_id}
    if detail:
        users = {u.id: u.name for u in db.scalars(select(m.User))}
        d.update({"question": it.question, "summary": it.summary, "params": it.params, "source_count": it.source_count,
                  "snapshot": it.snapshot,
                  "versions": [{"id": v.id, "label": v.label, "note": v.note, "created_at": v.created_at} for v in it.versions],
                  "shared_with": [{"id": s.id, "user": users.get(s.user_id), "group": s.group, "permission": s.permission} for s in it.shares],
                  "notes": [{"id": n.id, "user": users.get(n.user_id), "text": n.text, "created_at": n.created_at} for n in it.notes]})
    return d


@router.get("/workspace/items")
def list_items(db: DB, user: CurrentUser, library: Literal["all", "my", "shared", "archived"] = "all",
               type: str | None = None, module: str | None = None, folder: str | None = None, q: str | None = None,
               sort: Literal["updated", "title"] = "updated", desc: bool = True):
    items = db.scalars(select(m.WorkspaceItem)).all()  # fine at prototype scale; push into SQL + index when large
    out = []
    for it in items:
        acc = access_of(it, user)
        if not acc or it.archived != (library == "archived"):
            continue
        if library == "my" and acc != "Owner":
            continue
        if library == "shared" and not (acc != "Owner" or it.shares):
            continue
        if type and it.item_type != type or module and it.module != module or folder and it.folder != folder:
            continue
        if q and q.lower() not in f"{it.title} {it.question} {it.item_type}".lower():
            continue
        out.append(item_out(it, acc, db))
    out.sort(key=lambda x: x["title"].lower() if sort == "title" else x["updated_at"], reverse=desc)
    counts = {"all": 0, "my": 0, "shared": 0, "archived": 0}
    for it in items:
        acc = access_of(it, user)
        if not acc:
            continue
        if it.archived:
            counts["archived"] += 1
        else:
            counts["all"] += 1
            counts["my"] += acc == "Owner"
            counts["shared"] += acc != "Owner" or bool(it.shares)
    return {"counts": counts, "items": out, "folders": sorted({i.folder for i in items if i.folder and access_of(i, user)})}


@router.get("/workspace/items/{item_id}")
def item_detail(item_id: int, db: DB, user: CurrentUser):
    it, acc = get_item(db, item_id, user)
    return item_out(it, acc, db, detail=True)  # reopening returns the SAVED snapshot - it never reruns research


class ItemPatch(BaseModel):
    title: str | None = None
    folder: str | None = None
    archived: bool | None = None


@router.patch("/workspace/items/{item_id}")
def patch_item(item_id: int, body: ItemPatch, db: DB, user: m.User = Depends(require("edit"))):
    it, acc = get_item(db, item_id, user, "owner")
    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(422, "Name cannot be empty")
        it.title = body.title.strip()
    if body.folder is not None:
        it.folder = body.folder
    if body.archived is not None:
        it.archived = body.archived
    db.commit()
    return item_out(it, acc, db)


@router.delete("/workspace/items/{item_id}", status_code=204)
def delete_item(item_id: int, db: DB, user: m.User = Depends(require("edit"))):
    it, _ = get_item(db, item_id, user, "owner")
    audit(db, user, f"Deleted workspace item '{it.title}'", "workspace_item", it.id)
    db.delete(it)
    db.commit()


@router.post("/workspace/items/{item_id}/duplicate")
def duplicate(item_id: int, db: DB, user: m.User = Depends(require("create"))):
    it, _ = get_item(db, item_id, user)
    n = m.WorkspaceItem(owner_id=user.id, title=it.title + " (copy)", item_type=it.item_type, module=it.module,
                        folder=it.folder, status=it.status, question=it.question, summary=it.summary,
                        params=it.params, snapshot=it.snapshot, source_count=it.source_count)
    n.versions.append(m.ItemVersion(label="v1", note=f"Duplicated from {it.title}", snapshot=it.snapshot))
    db.add(n)
    db.commit()
    return item_out(n, "Owner", db)


@router.post("/workspace/items/{item_id}/versions/{version_id}/restore")
def restore_version(item_id: int, version_id: int, db: DB, user: m.User = Depends(require("edit"))):
    it, _ = get_item(db, item_id, user, "owner")
    v = next((x for x in it.versions if x.id == version_id), None)
    if not v:
        raise HTTPException(404, "Version not found")
    label = f"v{len(it.versions) + 1}"
    it.snapshot = v.snapshot
    it.versions.insert(0, m.ItemVersion(label=label, note=f"Restored {v.label}", snapshot=v.snapshot))
    db.commit()
    return {"restored": v.label, "new_version": label}


class ShareIn(BaseModel):
    user_id: int | None = None
    group: str | None = None
    permission: Literal["view", "edit"] = "view"


@router.post("/workspace/items/{item_id}/share")
def share(item_id: int, body: ShareIn, db: DB, user: m.User = Depends(require("share"))):
    it, _ = get_item(db, item_id, user, "owner")
    if not (body.user_id or body.group):
        raise HTTPException(422, "Choose a colleague or a group")
    s = next((x for x in it.shares if x.user_id == body.user_id and x.group == body.group), None)
    if s:
        s.permission = body.permission
    else:
        it.shares.append(m.ItemShare(user_id=body.user_id, group=body.group, permission=body.permission))
        if body.user_id:
            db.add(m.Notification(user_id=body.user_id, title="Item shared with you", route="workspace",
                                  body=f'{user.name} shared "{it.title}" with you'))
    db.commit()
    return {"ok": True}


@router.delete("/workspace/items/{item_id}/share/{share_id}", status_code=204)
def unshare(item_id: int, share_id: int, db: DB, user: m.User = Depends(require("share"))):
    it, _ = get_item(db, item_id, user, "owner")
    it.shares = [s for s in it.shares if s.id != share_id]
    db.commit()


class NoteIn(BaseModel):
    text: str


@router.post("/workspace/items/{item_id}/notes", status_code=201)
def add_note(item_id: int, body: NoteIn, db: DB, user: CurrentUser):
    it, _ = get_item(db, item_id, user)  # any collaborator, including view-only, may comment
    if not body.text.strip():
        raise HTTPException(422, "Write a note first")
    it.notes.append(m.ItemNote(user_id=user.id, text=body.text.strip()))
    db.commit()
    return {"ok": True}


@router.get("/users/directory")
def directory(db: DB, user: CurrentUser):
    """Share dialog picker."""
    users = db.scalars(select(m.User).where(m.User.status == "Active", m.User.id != user.id)).all()
    return {"users": [{"id": u.id, "name": u.name} for u in users],
            "groups": sorted({g for u in db.scalars(select(m.User)) for g in (u.groups or [])})}


# ================================================================== HISTORY
def _range_start(rng: str):
    return None if rng == "0" else (utc_now().replace(hour=0, minute=0, second=0, microsecond=0) if rng == "1"
                                    else utc_now() - timedelta(days=int(rng)))


@router.get("/history/summary")
def history_summary(db: DB, user: CurrentUser):
    wk = utc_now() - timedelta(days=7)
    runs = db.scalars(select(m.Run).where(m.Run.user_id == user.id, m.Run.deleted.is_(False))).all()
    return {"activities_this_week": sum(1 for r in runs if r.created_at >= wk),
            "needs_attention": sum(1 for r in runs if r.status in ("In Progress", "Paused", "Failed")),
            "reports_generated": sum(1 for r in runs if r.module == "cioa" and r.status == "Completed"),
            "searches_this_week": sum(1 for r in runs if r.module == "ai" and r.created_at >= wk)}


@router.get("/history/searches")
def history_searches(db: DB, user: CurrentUser, q: str | None = None, range: str = "30", mode: str | None = None):
    stmt = select(m.Run).where(m.Run.user_id == user.id, m.Run.module == "ai", m.Run.deleted.is_(False))
    if (start := _range_start(range)):
        stmt = stmt.where(m.Run.created_at >= start)
    if mode and mode != "All":
        stmt = stmt.where(m.Run.mode == mode)
    if q:
        stmt = stmt.where(m.Run.question.ilike(f"%{q}%"))
    rows = db.scalars(stmt.order_by(m.Run.created_at.desc())).all()
    return [{"id": r.id, "question": r.question, "mode": r.mode, "sources": r.source_count, "saved": r.saved,
             "context": r.params.get("context"), "created_at": r.created_at} for r in rows]


@router.get("/history/activity")
def history_activity(db: DB, user: CurrentUser, q: str | None = None, range: str = "30", module: str | None = None,
                     project: str | None = None, kind: str | None = None):
    stmt = select(m.Run).where(m.Run.user_id == user.id, m.Run.deleted.is_(False))
    if (start := _range_start(range)):
        stmt = stmt.where(m.Run.created_at >= start)
    if module and module != "All":
        stmt = stmt.where(m.Run.module == module)
    if project and project != "All":
        stmt = stmt.where(m.Run.project == project)
    if kind and kind != "All":
        stmt = stmt.where(m.Run.kind == kind)
    if q:
        stmt = stmt.where(or_(m.Run.title.ilike(f"%{q}%"), m.Run.step_label.ilike(f"%{q}%")))
    rows = db.scalars(stmt.order_by(m.Run.created_at.desc())).all()
    return [{"id": r.id, "kind": r.kind, "title": r.title, "module": r.module, "project": r.project, "status": r.status,
             "step_label": r.step_label, "resumable": r.status in ("In Progress", "Paused", "Failed"),
             "has_result": r.status == "Completed", "created_at": r.created_at} for r in rows]


@router.delete("/history", status_code=204)
def clear_history(db: DB, user: CurrentUser, scope: Literal["searches", "activity"] = "searches"):
    """Soft-deletes history rows. Saved searches and Workspace items are untouched."""
    stmt = select(m.Run).where(m.Run.user_id == user.id)
    if scope == "searches":
        stmt = stmt.where(m.Run.module == "ai")
    for r in db.scalars(stmt):
        r.deleted = True
    db.commit()
