"""Header + platform services: login, profile, research context chip, preferences, notifications, help."""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import DB, CurrentUser, audit
from app.core.security import create_token, verify_password
from app.db import models as m

router = APIRouter(tags=["account"])

GEOS = ["United States", "EU5", "Japan", "China", "Global"]
ALL_INDICATIONS = ["SLE", "RA", "AD", "Lupus Nephritis", "Psoriasis", "Hidradenitis Suppurativa", "Ulcerative Colitis"]


class LoginIn(BaseModel):
    email: str
    password: str


class ContextIn(BaseModel):
    target: str = Field(min_length=1, max_length=80)
    mechanism: str | None = None
    indications: list[str]
    geography: str = "United States"
    session: str = ""


class PrefsIn(BaseModel):
    deep_research: bool = False
    in_app_notifications: bool = True
    email: bool = False


def user_out(u: m.User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "groups": u.groups,
            "context": u.context, "prefs": u.prefs}


@router.post("/auth/login")
def login(body: LoginIn, db: DB):
    u = db.scalar(select(m.User).where(m.User.email == body.email.lower()))
    if not u or u.status != "Active" or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Invalid credentials")
    u.last_login = datetime.now(timezone.utc)
    db.commit()
    return {"access_token": create_token(u.id, u.role), "token_type": "bearer", "user": user_out(u)}


@router.get("/me")
def me(user: CurrentUser):
    return user_out(user)


@router.get("/context/options")
def context_options(user: CurrentUser):
    return {"indications": ALL_INDICATIONS, "geographies": GEOS}


@router.put("/me/context")
def set_context(body: ContextIn, db: DB, user: CurrentUser):
    bad = [i for i in body.indications if i not in ALL_INDICATIONS]
    if bad:
        raise HTTPException(422, f"Unknown indications: {bad}")
    user.context = {**body.model_dump(), "mechanism": body.mechanism or f"{body.target} inhibition"}
    db.commit()
    return user.context


@router.put("/me/preferences")
def set_prefs(body: PrefsIn, db: DB, user: CurrentUser):
    user.prefs = body.model_dump()
    db.commit()
    return user.prefs


# ------------------------------------------------------------ notifications (bell)
@router.get("/notifications")
def notifications(db: DB, user: CurrentUser, limit: int = 20):
    rows = db.scalars(select(m.Notification).where(m.Notification.user_id == user.id)
                      .order_by(m.Notification.id.desc()).limit(limit)).all()
    return {"unread": sum(1 for r in rows if r.unread),
            "items": [{"id": r.id, "title": r.title, "body": r.body, "route": r.route, "unread": r.unread,
                       "created_at": r.created_at} for r in rows]}


@router.post("/notifications/{nid}/read")
def read_one(nid: int, db: DB, user: CurrentUser):
    n = db.get(m.Notification, nid)
    if not n or n.user_id != user.id:
        raise HTTPException(404, "Not found")
    n.unread = False
    db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def read_all(db: DB, user: CurrentUser):
    for n in db.scalars(select(m.Notification).where(m.Notification.user_id == user.id, m.Notification.unread.is_(True))):
        n.unread = False
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------ help center + AI help chat
HELP_RULES = [
    (r"context|target|indication|geograph", "Your research context is in the header chip; select it to change target, mechanism, indications and geography."),
    (r"save|workspace|folder|share", "Use Save on any result to store it in Workspace; shared items keep their permission (view/edit)."),
    (r"score|scoring|weight", "Prioritization Scoring: set weights to total 100%, then Apply & Recalculate. Overrides need a justification."),
]


@router.get("/help/content")
def help_content(user: CurrentUser):
    return {"faq": [], "docs": [], "guides": []}  # TODO: serve from admin-managed content


class HelpChatIn(BaseModel):
    message: str


@router.post("/help/chat")
def help_chat(body: HelpChatIn, user: CurrentUser):
    for pat, ans in HELP_RULES:  # replace with an LLM + docs retrieval
        if re.search(pat, body.message, re.I):
            return {"reply": ans}
    return {"reply": "I can help with context, searching, saving and sharing, history, scoring and pipelines."}
