"""API smoke tests. Needs the full requirements installed: `pytest tests/test_api.py`."""
import os
os.environ.update(DATABASE_URL="sqlite:///./test.db", SEED_DEMO_DATA="true")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test.db"):
        os.remove("test.db")
    with TestClient(app) as c:
        yield c


def auth(client, email="swanik@example.com"):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "ChangeMe123!"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_requires_auth(client):
    assert client.get("/api/v1/me").status_code == 401


def test_ai_run_lifecycle_save_and_history(client):
    h = auth(client)
    r = client.post("/api/v1/runs", headers=h, json={"module": "ai", "question": "Which indications have high unmet need?"})
    assert r.status_code == 202
    rid = r.json()["id"]
    # TestClient executes BackgroundTasks before returning, so the run is already complete
    res = client.get(f"/api/v1/runs/{rid}/result", headers=h)
    assert res.status_code == 200 and res.json()["result"]["key_findings"]
    assert client.post(f"/api/v1/runs/{rid}/save", headers=h, json={}).json()["item_id"]
    assert client.get("/api/v1/history/searches", headers=h).json()[0]["id"] == rid


def test_pipeline_filtering(client):
    h = auth(client)
    d = client.get("/api/v1/pipeline/assets?indication=SLE&view=heatmap", headers=h).json()
    assert d["total"] == 4 and d["kpis"]["indications"] == 1


def test_scoring_flow(client):
    h = auth(client)
    model = client.get("/api/v1/scoring/models", headers=h).json()[0]["id"]
    a = client.post("/api/v1/scoring/assessments", headers=h, json={"model_id": model}).json()
    assert [r["indication"] for r in sorted(a["results"], key=lambda r: r["rank"])][0] == "SLE"
    bad = client.put(f"/api/v1/scoring/assessments/{a['id']}/weights", headers=h,
                     json={"weights": {c["key"]: 25 for c in a["criteria"]}})
    assert bad.status_code == 422
    no_just = client.put(f"/api/v1/scoring/assessments/{a['id']}/overrides", headers=h,
                         json={"indication": "RA", "criterion": "population", "score": 1})
    assert no_just.status_code == 422


def test_rbac(client):
    assert client.get("/api/v1/admin/users", headers=auth(client)).status_code == 403
    assert client.get("/api/v1/admin/users", headers=auth(client, "maria.chen@example.com")).status_code == 200
