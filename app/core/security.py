import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        s, h = stored.split("$")
        salt, expected = base64.b64decode(s), base64.b64decode(h)
    except Exception:
        return False
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(dk, expected)


ACCESS, REFRESH = "access", "refresh"


def create_token(user_id: int, role: str) -> str:
    """Access token: sent as 'Authorization: Bearer' on every API call."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    # jti = unique token id, so a single token can be revoked on logout
    return jwt.encode({"sub": str(user_id), "role": role, "exp": exp, "jti": uuid.uuid4().hex, "typ": ACCESS},
                      settings.jwt_secret, algorithm="HS256")


def create_refresh_token(user_id: int) -> str:
    """Refresh token: only accepted by POST /auth/refresh, which exchanges it (once) for a new token pair."""
    exp = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_expire_days)
    return jwt.encode({"sub": str(user_id), "exp": exp, "jti": uuid.uuid4().hex, "typ": REFRESH},
                      settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
