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


def test_workspace_search_matches_owner(client):
    h = auth(client)
    run = client.post("/api/v1/runs", headers=h, json={"module": "ai", "question": "Owner search regression"})
    assert run.status_code == 202
    item_id = client.post(f"/api/v1/runs/{run.json()['id']}/save", headers=h, json={}).json()["item_id"]

    results = client.get("/api/v1/workspace/items", headers=h, params={"q": "sWaNiK"}).json()["items"]

    assert any(item["id"] == item_id for item in results)


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


# ------------------------------------------------------------------ v0.2: updated UI design
def run(client, h, **body):
    r = client.post("/api/v1/runs", headers=h, json=body)
    assert r.status_code == 202, r.text
    return r.json()


def test_ai_loader_steps_sources_and_result_shape(client):
    h = auth(client)
    created = run(client, h, module="ai", question="What role does IRAK4 play in SLE?", deep_research=True)
    names = [s["name"] for s in created["steps"]]
    assert names == ["Interpret question", "Search literature", "Evaluate clinical evidence", "Cross-check findings",
                     "Compare indications", "Draft answer"]
    # one sub-item per searched source, defaulting to the module's sources that are enabled
    assert [i["label"] for i in created["steps"][1]["items"]] == ["PubMed", "ClinicalTrials.gov", "Open Targets Platform",
                                                                  "Citeline Trialtrove"]
    done = client.get(f"/api/v1/runs/{created['id']}/result", headers=h).json()
    assert all(i["status"] == "done" for s in done["steps"] for i in s["items"])
    res = done["result"]
    assert res["relevant"] and res["answer"] and len(res["follow_ups"]) == 3
    assert {s["name"] for s in res["top_sources"]} == set(res["research_summary"]["sources_searched"])
    assert res["research_summary"]["stage_count"] == 6


def test_out_of_scope_question_is_not_researched(client):
    h = auth(client)
    created = run(client, h, module="ai", question="What will the weather be in Paris tomorrow?")
    d = client.get(f"/api/v1/runs/{created['id']}/result", headers=h).json()
    assert d["result"]["relevant"] is False and d["result"]["follow_ups"]
    assert [s["status"] for s in d["steps"]][1:] == ["skipped"] * (len(d["steps"]) - 1)


def test_follow_up_thread_and_notify(client):
    h = auth(client)
    first = run(client, h, module="ai", question="Which biomarkers predict response to IRAK4 inhibition?")
    second = run(client, h, module="ai", question="And what about safety?", parent_run_id=first["id"], notify=True)
    assert second["thread_id"] == first["id"] and second["notify"] is True
    thread = client.get(f"/api/v1/runs/{second['id']}/thread", headers=h).json()
    assert [t["id"] for t in thread["turns"]] == [first["id"], second["id"]]
    # follow-ups never repeat a question already asked in the thread
    assert first["question"] not in thread["turns"][1]["result"]["follow_ups"]
    assert client.post(f"/api/v1/runs/{second['id']}/notify", headers=h, json={"enabled": False}).json()["notify"] is False
    bad = client.post("/api/v1/runs", headers=h, json={"module": "pipeline", "question": "x y", "parent_run_id": first["id"]})
    assert bad.status_code == 422


def test_rephrase(client):
    h = auth(client)
    r = client.post("/api/v1/prompts/rephrase", headers=h, json={"text": "irak4 biomarkers target engagement", "module": "ai"})
    assert "biomarkers" in r.json()["rephrased"].lower() and r.json()["rephrased"] != r.json()["original"]
    assert client.post("/api/v1/prompts/rephrase", headers=h, json={"text": "  "}).status_code == 422


def test_competitive_insights_workflow_run_change_and_report(client):
    h = auth(client)
    wfs = client.get("/api/v1/cioa/workflows", headers=h, params={"question": "Which endpoints are used?"}).json()
    assert len(wfs["workflows"]) == 5 and wfs["recommended"] == "endpoint"
    opts = client.get("/api/v1/cioa/options", headers=h).json()
    assert "AD" in opts["indications_by_area"]["Dermatology"]
    created = run(client, h, module="cioa", question="Map IRAK4 competitors in SLE", workflow="competitive",
                  indication="SLE", therapeutic_area="Rheumatology", molecular_target="IRAK4 inhibitor")
    assert created["steps"][2]["name"] == "Run Competitive Opportunity analysis"
    res = client.get(f"/api/v1/runs/{created['id']}/result", headers=h).json()["result"]
    assert res["workflow"]["key"] == "competitive" and len(res["sections"]) == 11 and len(res["stats"]) == 3
    assert res["context"] == {"therapeutic_area": "Rheumatology", "indications": ["SLE"], "molecular_target": "IRAK4 inhibitor"}
    report = client.get(f"/api/v1/runs/{created['id']}/report", headers=h).json()
    assert report["contents"][0] == "Peer Set Definition" and report["filename"].endswith(".pdf")
    changed = client.post(f"/api/v1/runs/{created['id']}/change-workflow", headers=h, json={"workflow": "unmet_need"})
    assert changed.status_code == 202 and changed.json()["thread_id"] == created["thread_id"]
    assert client.post(f"/api/v1/runs/{created['id']}/change-workflow", headers=h,
                       json={"workflow": "nope"}).status_code == 422
    assert client.post("/api/v1/runs", headers=h, json={"module": "cioa", "question": "q", "workflow": "nope"}).status_code == 422


def test_pipeline_new_filters(client):
    h = auth(client)
    opts = client.get("/api/v1/pipeline/options", headers=h).json()
    assert opts["stages"][0] == "Discovery" and opts["more_filters"][0]["key"] == "status"
    disc = client.get("/api/v1/pipeline/assets", headers=h, params={"stage": "Discontinued"}).json()
    assert disc["total"] == 1 and disc["data"][0]["activity"] == "Terminated"
    p2 = client.post("/api/v1/pipeline/assets/query", headers=h,
                     json={"filters": {"indication": "SLE"}, "more_filters": {"phase": ["p2"], "age": ["adult"]}}).json()
    assert {a["stage"] for a in p2["data"]} == {"Phase 2"} and p2["more_filters_count"] == 2
    assert p2["kpi_tiles"][0]["label"] == "Indication"
    created = run(client, h, module="pipeline", question="", filters={"indication": "AD"})
    res = client.get(f"/api/v1/runs/{created['id']}/result", headers=h).json()["result"]
    assert res["relevant"] and {a["indication"] for a in res["assets"]} == {"AD"} and res["follow_ups"]


def test_home_summary_and_run_again(client):
    h = auth(client)
    s = client.get("/api/v1/home/summary", headers=h).json()
    assert [c["key"] for c in s["cards"]] == ["in_progress", "recent_results", "saved"]
    created = run(client, h, module="ai", question="IRAK4 safety risks of long-term inhibition")
    item_id = client.post(f"/api/v1/runs/{created['id']}/save", headers=h, json={}).json()["item_id"]
    saved = client.get("/api/v1/home", headers=h, params={"tab": "saved"}).json()
    row = next(r for r in saved["items"] if r["id"] == item_id)
    assert row["action"] == "run_again" and row["module_name"] == "AI Scientific Search"
    again = client.post(f"/api/v1/workspace/items/{item_id}/rerun", headers=h)
    assert again.status_code == 202 and again.json()["question"] == created["question"]


def test_help_and_admin_overview(client):
    h = auth(client)
    help_ = client.get("/api/v1/help/content", headers=h).json()
    assert help_["faq"] and help_["guides"][0]["step"] == 1
    ov = client.get("/api/v1/admin/overview", headers=auth(client, "maria.chen@example.com")).json()
    wf = next(x for x in ov["sections"] if x["key"] == "workflow")
    assert wf["count"] == 5 and wf["badge"] == "3 live"


def test_profile_and_logout(client):
    h = auth(client, "sam.patel@example.com")
    p = client.get("/api/v1/me/profile", headers=h).json()
    assert p["role"] == "Reviewer" and "share" in p["permissions"] and p["last_login"] and p["initials"] == "SP"
    upd = client.patch("/api/v1/me/profile", headers=h, json={"name": "Sam R. Patel"})
    assert upd.status_code == 200 and upd.json()["name"] == "Sam R. Patel"
    assert client.patch("/api/v1/me/profile", headers=h, json={"name": "   "}).status_code == 422
    other = auth(client, "sam.patel@example.com")       # a second session for the same user
    assert client.post("/api/v1/auth/logout", headers=h).status_code == 204
    assert client.get("/api/v1/me", headers=h).status_code == 401      # this token is revoked
    assert client.post("/api/v1/auth/logout", headers=h).status_code == 401
    assert client.get("/api/v1/me", headers=other).status_code == 200  # other sessions keep working
    assert client.post("/api/v1/auth/logout").status_code == 401
