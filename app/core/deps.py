from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import REFRESH, decode_token
from app.db import models as m
from app.db.base import get_db

bearer = HTTPBearer(auto_error=False)
DB = Annotated[Session, Depends(get_db)]

# Role -> permission mapping lives in the roles / permissions / role_permissions tables
# (seeded from app.services.catalog.ROLES; editable without a code change).


def token_claims(db: DB, cred: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> dict:
    """Decoded, non-revoked token claims (sub, role, exp, jti)."""
    if not cred:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        claims = decode_token(cred.credentials)
        int(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if claims.get("typ") == REFRESH:  # tokens issued before refresh support have no typ: they are access tokens
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Use the access token here, not the refresh token")
    if claims.get("jti") and db.get(m.RevokedToken, claims["jti"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Signed out. Please sign in again")
    return claims


Claims = Annotated[dict, Depends(token_claims)]


def current_user(db: DB, claims: Claims) -> m.User:
    uid = int(claims["sub"])
    user = db.get(m.User, uid)
    if not user or user.status != "Active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User inactive")
    return user


CurrentUser = Annotated[m.User, Depends(current_user)]


def require(perm: str):
    def dep(user: CurrentUser) -> m.User:
        if perm not in user.permission_codes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires '{perm}' permission")
        return user
    return dep


def audit(db, user: m.User | None, action: str, entity: str = "", entity_id="", before=None, after=None) -> None:
    # snapshots may contain dates and other non-JSON values: store them in their JSON (API) form
    before, after = jsonable_encoder(before), jsonable_encoder(after)
    db.add(m.AuditLog(user_id=user.id if user else None, action=action[:300], entity=entity[:40],
                      entity_id=str(entity_id), before=before, after=after))
    db.commit()
