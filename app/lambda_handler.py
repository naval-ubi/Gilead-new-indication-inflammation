"""AWS Lambda entry point: handler = "app.lambda_handler.handler".

One function serves these events:
  * API Gateway (HTTP API or REST API) requests: passed to the FastAPI app through Mangum.
  * {"task": "execute_run", "run_id": N}: a research run started by the API (app/services/tasks.py), executed here
    so it is not limited by API Gateway's ~30 second request timeout.
  * {"task": "init"}: returns once start-up has finished. Invoke it once after a deployment to create the tables
    and demo data without API Gateway's time limit (aws lambda invoke ... --payload '{"task": "init"}').

Start-up work (database migrations, demo data) runs once per cold start, not on every request.
"""
from __future__ import annotations

import logging

from mangum import Mangum

from app.core.config import settings
from app.main import app, startup
from app.services import tasks

logging.getLogger().setLevel(logging.INFO)


def check_settings() -> None:
    """Fail the cold start with a clear message instead of serving with an unusable configuration."""
    problems = []
    if settings.database_url.startswith("sqlite"):
        problems.append("DATABASE_URL points to SQLite. Lambda has no shared disk: use PostgreSQL (Amazon RDS), "
                        "e.g. postgresql+psycopg://user:password@host:5432/gilead")
    if settings.jwt_secret == "dev-secret-change-me":
        problems.append("JWT_SECRET is the development default. Set a long random value.")
    if problems:
        raise RuntimeError("Configuration error: " + " | ".join(problems))


if settings.on_lambda:
    check_settings()
startup()

# lifespan="off": Mangum would otherwise run start-up and shut-down on every request
_http = Mangum(app, lifespan="off")


def handler(event, context):
    if isinstance(event, dict) and event.get("task") == tasks.TASK:
        return tasks.handle_task(event)
    if isinstance(event, dict) and event.get("task") == "init":
        return {"ready": True}            # start-up (migrations, demo data) ran during this cold start
    return _http(event, context)
