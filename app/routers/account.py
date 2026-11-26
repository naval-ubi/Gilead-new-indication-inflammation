"""Header + platform services: login, profile, research context chip, preferences, notifications, help."""
import re
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DB, Claims, CurrentUser, audit
from app.core.security import REFRESH, create_refresh_token, create_token, decode_token, verify_password
from app.db import models as m
from app.services import catalog

router = APIRouter(tags=["account"])

GEOS = catalog.GEOGRAPHIES
ALL_INDICATIONS = catalog.ALL_INDICATIONS


class LoginIn(BaseModel):
    email: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str | None = None


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
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role.name, "groups": u.group_names,
            "permissions": sorted(u.permission_codes), "context": u.context, "prefs": u.prefs}


@router.post("/auth/login")
def login(body: LoginIn, db: DB):
    u = db.scalar(select(m.User).where(m.User.email == body.email.lower()))
    if not u or u.status != "Active" or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Invalid credentials")
    u.last_login = datetime.now(timezone.utc)
    db.commit()
    return {**token_pair(u), "user": user_out(u)}


def token_pair(u: m.User) -> dict:
    return {"access_token": create_token(u.id, u.role.name), "refresh_token": create_refresh_token(u.id),
            "token_type": "bearer"}


def _revoke(db, claims: dict, user_id: int) -> None:
    """Adds the token's jti to revoked_tokens (no commit). Rows are needed only until the token would expire."""
    if claims.get("jti") and not db.get(m.RevokedToken, claims["jti"]):
        db.add(m.RevokedToken(jti=claims["jti"], user_id=user_id,
                              expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc)))


def _refresh_claims(db, token: str) -> dict:
    """Decoded claims of a valid, unused refresh token, else 401."""
    try:
        claims = decode_token(token)
        int(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid or expired refresh token. Please sign in again")
    if claims.get("typ") != REFRESH:
        raise HTTPException(401, "Not a refresh token")
    if db.get(m.RevokedToken, claims["jti"]):
        raise HTTPException(401, "Refresh token already used or signed out. Please sign in again")
    return claims


@router.post("/auth/refresh")
def refresh(body: RefreshIn, db: DB):
    """Exchanges a refresh token for a new access token AND a new refresh token (rotation): the refresh token sent
    is revoked, so each one works only once. The role in the new access token is read fresh from the database."""
    claims = _refresh_claims(db, body.refresh_token)
    u = db.get(m.User, int(claims["sub"]))
    if not u or u.status != "Active":
        raise HTTPException(401, "User inactive")
    _revoke(db, claims, u.id)
    try:
        db.commit()
    except IntegrityError:  # two refreshes with the same token at once: only the first wins
        db.rollback()
        raise HTTPException(401, "Refresh token already used. Please sign in again")
    return token_pair(u)


@router.post("/auth/logout", status_code=204)
def logout(db: DB, user: CurrentUser, claims: Claims, body: LogoutIn | None = None):
    """Header profile menu -> Logout. Revokes the token used for this call; any later call with it returns 401.
    Send {"refresh_token": ...} too so the session cannot be renewed. An invalid refresh token is ignored.
    Saved searches and Workspace items are untouched. Unsaved-edit warnings are handled by the UI before calling."""
    now = datetime.now(timezone.utc)
    # housekeeping: revoked rows are only needed until the token would have expired anyway
    for old in db.scalars(select(m.RevokedToken).where(m.RevokedToken.expires_at < now)):
        db.delete(old)
    _revoke(db, claims, user.id)
    if body and body.refresh_token:
        try:
            rc = decode_token(body.refresh_token)
            if rc.get("typ") == REFRESH and int(rc["sub"]) == user.id:
                _revoke(db, rc, user.id)
        except (jwt.PyJWTError, KeyError, ValueError):
            pass
    db.commit()
    audit(db, user, "Logged out", "user", user.id)


@router.get("/me")
def me(user: CurrentUser):
    return user_out(user)


@router.get("/me/profile")
def profile(user: CurrentUser):
    """'My Profile' dialog: identity, role, groups, permissions, active research session and last login."""
    initials = "".join(w[0] for w in user.name.split()[:2]).upper()
    return {**user_out(user), "initials": initials, "status": user.status, "last_login": user.last_login,
            "active_session": (user.context or {}).get("session", "")}


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.patch("/me/profile")
def update_profile(body: ProfileIn, db: DB, user: CurrentUser):
    """Edit display name. Email, role and groups are managed by administrators."""
    if not body.name.strip():
        raise HTTPException(422, "Name cannot be empty")
    before = {"name": user.name}
    user.name = body.name.strip()
    db.commit()
    audit(db, user, "Updated own profile", "user", user.id, before, {"name": user.name})
    return profile(user)


@router.get("/context/options")
def context_options(user: CurrentUser):
    return {"indications": ALL_INDICATIONS, "indication_groups": catalog.PIPELINE_FILTER_GROUPS["indication"],
            "indication_names": catalog.INDICATION_FULL_NAMES, "geographies": GEOS,
            "therapeutic_areas": list(catalog.THERAPEUTIC_AREAS), "molecular_targets": catalog.MOLECULAR_TARGET_GROUPS,
            "module_names": catalog.MODULE_NAMES}


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
HELP_RULES = catalog.HELP_CHAT_RULES
HELP_SUGGESTIONS = ["Change research context", "Save and share", "Resume work"]


@router.get("/help/content")
def help_content(user: CurrentUser):
    """Help menu: Documentation, User guides, FAQs, and the AI Help Chat quick replies.
    TODO: move into admin-managed content so it can be edited without a release."""
    pair = lambda rows, a, b: [{a: x, b: y} for x, y in rows]  # noqa: E731
    return {"docs": pair(catalog.HELP_DOCS, "module", "description"),
            "guides": [{"step": i + 1, "title": t, "body": b} for i, (t, b) in enumerate(catalog.HELP_GUIDES)],
            "faq": pair(catalog.HELP_FAQ, "question", "answer"),
            "chat_suggestions": HELP_SUGGESTIONS}


class HelpChatIn(BaseModel):
    message: str


@router.post("/help/chat")
def help_chat(body: HelpChatIn, user: CurrentUser):
    for pat, ans in HELP_RULES:  # replace with an LLM + docs retrieval
        if re.search(pat, body.message, re.I):
            return {"reply": ans}
    return {"reply": "I can help with context, searching, saving and sharing, history, scoring, pipeline views, "
                     "Competitive Insights workflows and exports. Try one of those, or open Help, then FAQs."}
