"""Configuration from AWS Secrets Manager (simulated: no AWS calls)."""
import json

import pytest

from app.core import aws_secrets
from app.core import config as cfg

APP_ARN = "arn:aws:secretsmanager:ap-south-1:123456789012:secret:gilead/app"
DB_ARN = "arn:aws:secretsmanager:ap-south-1:123456789012:secret:rds!db-1"


class FakeSecrets:
    def __init__(self, values: dict):
        self.values, self.reads = values, []

    def get_secret_value(self, SecretId):
        self.reads.append(SecretId)
        if SecretId not in self.values:
            raise KeyError("ResourceNotFoundException")
        return {"SecretString": json.dumps(self.values[SecretId])}


@pytest.fixture
def secrets(monkeypatch, tmp_path):
    fake = FakeSecrets({
        APP_ARN: {"JWT_SECRET": "s" * 64, "AI_SEARCH_BASE_URL": "https://ai.example.com"},
        DB_ARN: {"username": "gilead_admin", "password": "first-password"},     # RDS-managed master secret
    })
    monkeypatch.setattr(aws_secrets, "_client", lambda: fake)
    monkeypatch.setattr(aws_secrets, "_cache", {})
    monkeypatch.chdir(tmp_path)                       # no .env file
    for name in ("DATABASE_URL", "JWT_SECRET", "AI_SEARCH_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    return fake


def test_settings_from_app_and_rds_secrets(secrets, monkeypatch):
    monkeypatch.setenv("APP_SECRET_ARN", APP_ARN)
    monkeypatch.setenv("DB_SECRET_ARN", DB_ARN)
    monkeypatch.setenv("DB_HOST", "gilead.abc123.ap-south-1.rds.amazonaws.com")
    monkeypatch.setenv("DB_NAME", "gilead")
    s = cfg.load_settings()
    assert s.jwt_secret == "s" * 64 and s.ai_search_base_url == "https://ai.example.com"
    assert s.database_url == ("postgresql+psycopg://gilead_admin@gilead.abc123.ap-south-1.rds.amazonaws.com:5432/gilead"
                              "?sslmode=require")
    assert "first-password" not in s.database_url and s.database_from_secret


def test_explicit_environment_wins(secrets, monkeypatch):
    monkeypatch.setenv("APP_SECRET_ARN", APP_ARN)
    monkeypatch.setenv("DB_SECRET_ARN", DB_ARN)
    monkeypatch.setenv("JWT_SECRET", "from-environment")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@other:5432/x")
    s = cfg.load_settings()
    assert s.jwt_secret == "from-environment"
    assert s.database_url == "postgresql+psycopg://u:p@other:5432/x" and not s.database_from_secret


def test_standard_rds_secret_format_and_quoting(secrets):
    secrets.values[DB_ARN] = {"engine": "postgres", "host": "db.internal", "port": 5433, "dbname": "gilead",
                              "username": "app@user", "password": "x"}
    assert aws_secrets.rds_database_url(DB_ARN) == "postgresql+psycopg://app%40user@db.internal:5433/gilead?sslmode=require"


def test_missing_host_is_explained(secrets):
    with pytest.raises(RuntimeError, match="host \\(DB_HOST\\)"):
        aws_secrets.rds_database_url(DB_ARN, name="gilead")


def test_unreadable_secret_is_explained_without_values(secrets):
    with pytest.raises(RuntimeError, match="Could not read secret .*nope"):
        aws_secrets.get_secret("nope")


def test_secret_is_cached(secrets):
    aws_secrets.rds_password(DB_ARN)
    aws_secrets.rds_password(DB_ARN)
    assert secrets.reads == [DB_ARN]


def test_rotated_password_is_reread_once(secrets):
    class DBAPI:
        class OperationalError(Exception):
            pass

    class Dialect:
        loaded_dbapi = DBAPI
        tried = []

        def connect(self, *a, **kw):
            self.tried.append(kw["password"])
            if kw["password"] != "rotated-password":
                raise DBAPI.OperationalError('FATAL: password authentication failed for user "gilead_admin"')
            return "connection"

    d = Dialect()
    aws_secrets.rds_password(DB_ARN)                                   # cached: first-password
    secrets.values[DB_ARN]["password"] = "rotated-password"           # rotated in Secrets Manager
    assert aws_secrets.connect_with_rds_password(d, (), {}, DB_ARN) == "connection"
    assert d.tried == ["first-password", "rotated-password"]
