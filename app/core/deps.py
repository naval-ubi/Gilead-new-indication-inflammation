from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db import models as m
from app.db.base import get_db

bearer = HTTPBearer(auto_error=False)
DB = Annotated[Session, Depends(get_db)]

# role -> permissions (matches the Admin note: view, create, edit, execute, publish, share, administer)
PERMS = {
    "Admin": {"view", "create", "edit", "execute", "publish", "share", "administer"},
    "Analyst": {"view", "create", "edit", "execute", "share"},
    "Reviewer": {"view", "edit", "share"},
    "Viewer": {"view"},
}


def current_user(db: DB, cred: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> m.User:
    if not cred:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        uid = int(decode_token(cred.credentials)["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = db.get(m.User, uid)
    if not user or user.status != "Active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User inactive")
    return user


CurrentUser = Annotated[m.User, Depends(current_user)]


def require(perm: str):
    def dep(user: CurrentUser) -> m.User:
        if perm not in PERMS.get(user.role, set()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires '{perm}' permission")
        return user
    return dep


def audit(db, user: m.User | None, action: str, entity: str = "", entity_id="", before=None, after=None) -> None:
    db.add(m.AuditLog(user_id=user.id if user else None, action=action, entity=entity,
                      entity_id=str(entity_id), before=before, after=after))
    db.commit()
