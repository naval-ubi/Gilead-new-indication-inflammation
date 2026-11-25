"""Configuration from AWS Secrets Manager (used on AWS; optional everywhere else).

Two secrets, both optional, both JSON:

APP_SECRET_ARN  application secrets as {"ENV_VAR_NAME": "value"}, e.g.
                {"JWT_SECRET": "...", "AI_SEARCH_BASE_URL": "https://...", "AI_SEARCH_API_KEY": ""}
                Each key fills the setting of the same name, unless that environment variable is set explicitly.

DB_SECRET_ARN   the Amazon RDS credentials secret: either the one RDS creates when "Manage master credentials in
                AWS Secrets Manager" is on ({"username", "password"}), or the standard RDS format
                ({"engine", "host", "port", "dbname", "username", "password"}). Host, port and database name come
                from the secret or from DB_HOST / DB_PORT / DB_NAME. The password is read when a database connection
                is opened (never stored in DATABASE_URL) and re-read when it is rejected, so rotation needs no
                redeploy. An explicit DATABASE_URL takes precedence over DB_SECRET_ARN.

Values are cached for SECRET_CACHE_SECONDS (5 minutes) per container.
"""
from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import quote

SECRET_CACHE_SECONDS = 300
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _client():
    import boto3  # in requirements-lambda.txt; also part of the AWS Lambda Python runtime

    return boto3.client("secretsmanager")


def get_secret(secret_id: str, refresh: bool = False) -> dict:
    """The secret's JSON value, cached per container."""
    with _lock:
        hit = _cache.get(secret_id)
        if hit and not refresh and time.monotonic() - hit[0] < SECRET_CACHE_SECONDS:
            return hit[1]
    try:
        raw = _client().get_secret_value(SecretId=secret_id)["SecretString"]
    except Exception as e:  # noqa: BLE001 - say which secret, never its value
        raise RuntimeError(f"Could not read secret {secret_id} from AWS Secrets Manager: {type(e).__name__}: {e}") from None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"Secret {secret_id} is not JSON. Store it as key/value pairs.") from None
    with _lock:
        _cache[secret_id] = (time.monotonic(), value)
    return value


def apply_app_secret(secret_id: str) -> list[str]:
    """Copy the application secret into environment variables that are not set yet. Returns the names applied."""
    applied = []
    for key, value in get_secret(secret_id).items():
        name = key.upper()
        if name not in os.environ and value is not None:
            os.environ[name] = str(value)
            applied.append(name)
    return applied


def rds_database_url(secret_id: str, host: str = "", port: int | None = None, name: str = "",
                     sslmode: str = "require") -> str:
    """SQLAlchemy URL for the RDS database, without the password (added per connection by rds_password)."""
    s = get_secret(secret_id)
    host = host or s.get("host", "")
    port = port or s.get("port") or 5432
    name = name or s.get("dbname", "")
    user = s.get("username", "")
    missing = [n for n, v in (("host (DB_HOST)", host), ("database name (DB_NAME)", name), ("username", user)) if not v]
    if missing:
        raise RuntimeError(f"RDS settings incomplete: {', '.join(missing)} not found in secret {secret_id} "
                           "or the environment.")
    url = f"postgresql+psycopg://{quote(user, safe='')}@{host}:{port}/{quote(name, safe='')}"
    return f"{url}?sslmode={sslmode}" if sslmode else url


def rds_password(secret_id: str, refresh: bool = False) -> str:
    return get_secret(secret_id, refresh=refresh).get("password", "")


def connect_with_rds_password(dialect, cargs, cparams, secret_id: str):
    """Opens a database connection with the password from the RDS secret. If the database rejects it (the secret
    was rotated after it was cached), the secret is read again once."""
    cparams["password"] = rds_password(secret_id)
    try:
        return dialect.connect(*cargs, **cparams)
    except dialect.loaded_dbapi.OperationalError as e:
        if "password authentication failed" not in str(e):
            raise
        cparams["password"] = rds_password(secret_id, refresh=True)
        return dialect.connect(*cargs, **cparams)
