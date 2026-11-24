"""Shared test setup. Runs before any test module is imported, so every test file uses the test database and never
the real gilead.db, whichever files are run.

SQLite test.db by default; TEST_DATABASE_URL=postgresql+psycopg://... runs the same tests on PostgreSQL.

The AI Scientific Search service is simulated in every test (no network, nothing billed): `service` stands in for
the API Gateway and answers with a real recorded reply (fixtures/ai_search_query.json). Tests that need the service
to be missing use the `no_service` fixture."""
import json
import os
from pathlib import Path

TEST_DB = os.environ.get("TEST_DATABASE_URL", "sqlite:///./test.db")
os.environ.update(DATABASE_URL=TEST_DB, SEED_DEMO_DATA="true", SEED_SAMPLE_DATA="false", AI_SEARCH_BASE_URL="")

import httpx  # noqa: E402
import pytest  # noqa: E402

REAL = json.loads((Path(__file__).parent / "fixtures" / "ai_search_query.json").read_text())
BASE = "https://ai-search.test"
REPHRASED = "Explain the role of IRAK4 in lupus."


class FakeService:
    """Stands in for the API Gateway. `replies` is a list of (status, body) or exceptions, used in order; when it is
    empty, /query answers with the recorded reply and /rephrase with REPHRASED."""
    def __init__(self):
        self.calls, self.replies = [], []

    def handler(self, request: httpx.Request):
        self.calls.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy", "service": "scientific-search-api", "message": "ok"})
        reply = self.replies.pop(0) if self.replies else (200, REAL)
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        if request.url.path.endswith("/rephrase") and status == 200 and body is REAL:
            body = {"status": "success", "original_query": "x", "suggested_query": REPHRASED}
        return httpx.Response(status, json=body)

    def queries(self) -> list[str]:
        return [c[2]["query"] for c in self.calls if c[1].endswith("/query")]


@pytest.fixture(autouse=True)
def service(monkeypatch):
    from app.core.config import settings
    from app.services import scientific_search as ss

    fake = FakeService()
    monkeypatch.setattr(settings, "ai_search_base_url", BASE)
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    monkeypatch.setattr(ss, "_client", lambda: httpx.Client(base_url=BASE, transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture
def no_service(monkeypatch, service):
    """AI_SEARCH_BASE_URL not set."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_search_base_url", "")
    return service
