"""Starts research runs in the background.

Locally (uvicorn, containers) a run executes after the HTTP response, in the same process (FastAPI BackgroundTasks).
On AWS Lambda that does not work: the response is only returned once background work finishes, and API Gateway
ends requests after ~30 seconds while a run takes 15-60 seconds. There the API invokes a Lambda function
asynchronously with {"task": "execute_run", "run_id": ...} and returns 202 at once; the function (by default the
same one, see app/lambda_handler.py) executes the run and saves progress to the database, which the frontend polls.
"""
from __future__ import annotations

import json
import logging
import os

from fastapi import BackgroundTasks

from app.core.config import settings

log = logging.getLogger(__name__)
TASK = "execute_run"


def _lambda_client():
    import boto3  # included in the AWS Lambda Python runtime

    return boto3.client("lambda")


def worker_function() -> str:
    return settings.worker_function_name or os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")


def submit_run(bg: BackgroundTasks, run_id: int) -> None:
    """Execute a run in the background. The run must already be committed."""
    if settings.dispatch_mode != "lambda":
        from app.services.orchestrator import execute_run
        bg.add_task(execute_run, run_id)
        return
    try:
        _lambda_client().invoke(FunctionName=worker_function(), InvocationType="Event",
                                Payload=json.dumps({"task": TASK, "run_id": run_id}).encode())
    except Exception as e:  # noqa: BLE001 - any failure: show it on the run, which stays resumable
        log.exception("Could not start run %s", run_id)
        _mark_not_started(run_id, f"Could not start the research worker ({type(e).__name__}). Please retry.")


def _mark_not_started(run_id: int, message: str) -> None:
    from app.db import models as m
    from app.db.base import SessionLocal

    with SessionLocal() as db:
        run = db.get(m.Run, run_id)
        if run:
            run.status, run.error = "Failed", message
            run.step_label = f"Failed at step {run.step_index + 1} - {message}"[:300]
            db.commit()


def handle_task(event: dict) -> dict:
    """Runs a task sent by submit_run (on the worker Lambda)."""
    from app.services.orchestrator import execute_run

    run_id = int(event["run_id"])
    execute_run(run_id)
    return {"task": TASK, "run_id": run_id, "done": True}
