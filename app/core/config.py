import os

from pydantic import PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    database_url: str = "sqlite:///./gilead.db"
    jwt_secret: str = "dev-secret-change-me"
    jwt_expire_minutes: int = 480        # access token lifetime
    jwt_refresh_expire_days: int = 7     # refresh token lifetime (POST /auth/refresh)
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    seed_demo_data: bool = True
    seed_sample_data: bool = False   # frontend testing: runs, saved items, sharing, scoring (app/seed_samples.py)
    # AI Scientific Search service (AWS API Gateway). Empty = research runs fail with "not configured"; AI Rephrase 503.
    ai_search_base_url: str = ""
    ai_search_timeout_seconds: float = 90
    ai_search_api_key: str = ""      # only if the gateway is protected with an API key (sent as x-api-key)
    # Where research runs execute. "inline": after the response, in the same process (uvicorn, containers).
    # "lambda": an asynchronous invocation of a Lambda function (API Gateway ends requests after ~30 s, runs take
    # 15-60 s). "auto" = "lambda" when running on AWS Lambda, else "inline".
    run_dispatch: str = "auto"
    worker_function_name: str = ""   # Lambda that executes runs; empty = this function (self-invocation)

    # AWS Secrets Manager (see app/core/aws_secrets.py). Both optional.
    app_secret_arn: str = ""         # JSON {"JWT_SECRET": ..., "AI_SEARCH_BASE_URL": ...}
    db_secret_arn: str = ""          # Amazon RDS credentials secret; used when DATABASE_URL is not set
    db_host: str = ""                # RDS endpoint, if not in the secret
    db_port: int | None = None
    db_name: str = ""                # database name, if not in the secret
    db_sslmode: str = "require"      # RDS: encrypt connections
    _database_from_secret: bool = PrivateAttr(default=False)

    @property
    def database_from_secret(self) -> bool:
        """True when the database URL was built from DB_SECRET_ARN (the password is added per connection)."""
        return self._database_from_secret

    @property
    def on_lambda(self) -> bool:
        return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

    @property
    def dispatch_mode(self) -> str:
        if self.run_dispatch in ("inline", "lambda"):
            return self.run_dispatch
        return "lambda" if self.on_lambda else "inline"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def load_settings() -> Settings:
    """Environment / .env first; then the application secret fills unset values; then the RDS secret builds the
    database URL when DATABASE_URL is not set explicitly."""
    from app.core import aws_secrets

    boot = Settings()
    if not boot.app_secret_arn and not boot.db_secret_arn:
        return boot
    if boot.app_secret_arn:
        aws_secrets.apply_app_secret(boot.app_secret_arn)
    s = Settings()
    if s.db_secret_arn and "database_url" not in s.model_fields_set:   # an explicit DATABASE_URL wins
        s.database_url = aws_secrets.rds_database_url(s.db_secret_arn, s.db_host, s.db_port, s.db_name, s.db_sslmode)
        s._database_from_secret = True
    return s


settings = load_settings()
