"""AWS Lambda entry point: API Gateway events through Mangum, and research runs handed to an asynchronous invocation
(simulated: no AWS calls)."""
import json

import pytest

from app.core.config import settings
from app.services import tasks


def http_event(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    """An API Gateway HTTP API (payload v2.0) event, as Lambda receives it."""
    headers = {"content-type": "application/json", "host": "abc123.execute-api.ap-south-1.amazonaws.com"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    return {
        "version": "2.0", "routeKey": "ANY /{proxy+}", "rawPath": path, "rawQueryString": "", "headers": headers,
        "requestContext": {"http": {"method": method, "path": path, "sourceIp": "203.0.113.1", "protocol": "HTTP/1.1",
                                    "userAgent": "test"}, "stage": "$default", "requestId": "r1",
                           "domainName": "abc123.execute-api.ap-south-1.amazonaws.com", "accountId": "123456789012",
                           "apiId": "abc123", "time": "24/Nov/2026:10:00:00 +0000", "timeEpoch": 0},
        "body": json.dumps(body) if body is not None else None, "isBase64Encoded": False,
    }


class Context:
    function_name, aws_request_id = "gilead-api", "req-1"
    memory_limit_in_mb, invoked_function_arn = 1024, "arn:aws:lambda:ap-south-1:123456789012:function:gilead-api"

    def get_remaining_time_in_millis(self):
        return 900_000


class FakeLambda:
    def __init__(self, fail=False):
        self.invocations, self.fail = [], fail

    def invoke(self, **kw):
        if self.fail:
            raise ConnectionError("no route to lambda.ap-south-1.amazonaws.com")
        self.invocations.append(kw)
        return {"StatusCode": 202}


@pytest.fixture(scope="module")
def lam():
    from app import lambda_handler  # cold start: prepares the (test) database
    return lambda_handler


@pytest.fixture
def on_lambda(monkeypatch):
    fake = FakeLambda()
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "gilead-api")
    monkeypatch.setattr(settings, "run_dispatch", "auto")
    monkeypatch.setattr(tasks, "_lambda_client", lambda: fake)
    return fake


def call(lam, method, path, body=None, token=None):
    out = lam.handler(http_event(method, path, body, token), Context())
    return out["statusCode"], json.loads(out["body"]) if out.get("body") else None


def login(lam):
    status, body = call(lam, "POST", "/api/v1/auth/login", {"email": "swanik@example.com", "password": "ChangeMe123!"})
    assert status == 200
    return body["access_token"]


def test_init_task(lam):
    assert lam.handler({"task": "init"}, Context()) == {"ready": True}


def test_api_gateway_request_is_served(lam):
    assert call(lam, "GET", "/health") == (200, {"status": "ok"})
    status, me = call(lam, "GET", "/api/v1/me", token=login(lam))
    assert status == 200 and me["email"] == "swanik@example.com"


def test_run_is_handed_to_async_invocation_then_executed(lam, on_lambda, service):
    token = login(lam)
    status, run = call(lam, "POST", "/api/v1/runs", {"module": "ai", "question": "What role does IRAK4 play in SLE?"}, token)
    assert status == 202 and run["status"] == "In Progress"
    inv = on_lambda.invocations[-1]
    assert inv["FunctionName"] == "gilead-api" and inv["InvocationType"] == "Event"
    payload = json.loads(inv["Payload"])
    assert payload == {"task": "execute_run", "run_id": run["id"]}
    assert service.queries() == []                          # nothing ran inside the API request
    assert call(lam, "GET", f"/api/v1/runs/{run['id']}", token=token)[1]["status"] == "In Progress"

    assert lam.handler(payload, Context())["done"] is True    # the worker invocation
    status, d = call(lam, "GET", f"/api/v1/runs/{run['id']}/result", token=token)
    assert status == 200 and d["result"]["provider"] == "ai_scientific_search"


def test_resume_is_also_handed_off(lam, on_lambda, service):
    token = login(lam)
    service.replies = [(500, {"status": "error", "message": "down"})]
    _, run = call(lam, "POST", "/api/v1/runs", {"module": "ai", "question": "IRAK4 biomarkers in RA"}, token)
    lam.handler(json.loads(on_lambda.invocations[-1]["Payload"]), Context())
    assert call(lam, "GET", f"/api/v1/runs/{run['id']}", token=token)[1]["status"] == "Failed"
    assert call(lam, "POST", f"/api/v1/runs/{run['id']}/resume", token=token)[0] == 202
    lam.handler(json.loads(on_lambda.invocations[-1]["Payload"]), Context())
    assert call(lam, "GET", f"/api/v1/runs/{run['id']}", token=token)[1]["status"] == "Completed"


def test_failed_hand_off_marks_run_failed_and_retryable(lam, on_lambda, monkeypatch):
    monkeypatch.setattr(tasks, "_lambda_client", lambda: FakeLambda(fail=True))
    token = login(lam)
    _, run = call(lam, "POST", "/api/v1/runs", {"module": "ai", "question": "IRAK4 degraders in AD"}, token)
    d = call(lam, "GET", f"/api/v1/runs/{run['id']}", token=token)[1]
    assert d["status"] == "Failed" and "Could not start the research worker" in d["step_label"] and d["resumable"]


def test_lambda_refuses_sqlite_and_default_secret(lam, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "sqlite:///./gilead.db")
    monkeypatch.setattr(settings, "jwt_secret", "dev-secret-change-me")
    with pytest.raises(RuntimeError, match="SQLite.*JWT_SECRET"):
        lam.check_settings()
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u:p@db:5432/gilead")
    monkeypatch.setattr(settings, "jwt_secret", "x" * 64)
    lam.check_settings()                                      # a valid configuration passes
