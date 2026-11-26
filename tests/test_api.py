"""API smoke tests. Needs the full requirements installed: `pytest tests/test_api.py`.

Runs on SQLite by default. To run the same tests on PostgreSQL, point TEST_DATABASE_URL at an empty test database
(its contents are dropped):  TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/gilead_test pytest
"""
import os
TEST_DB = os.environ["DATABASE_URL"]  # set by tests/conftest.py

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.base import engine
from app.main import app


def reset_database():
    if TEST_DB.startswith("sqlite"):
        if os.path.exists("test.db"):
            os.remove("test.db")
    else:
        with engine.begin() as conn:  # start from an empty schema (migrations run again on start-up)
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="module")
def client():
    reset_database()
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
    # the AI service cannot score yet: every AI score is Missing until the user enters one with a justification
    assert all(v is None for scores in a["ai_scores"].values() for v in scores.values())
    for ind, score in (("SLE", 5), ("RA", 2), ("AD", 1)):
        a = client.put(f"/api/v1/scoring/assessments/{a['id']}/overrides", headers=h, json={
            "indication": ind, "criterion": "population", "score": score, "justification": "Claims data"}).json()
    assert [r["indication"] for r in sorted(a["results"], key=lambda r: r["rank"] or 99)][0] == "SLE"
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
    # the search stage is one call to the AI service; afterwards it lists the sources the service used
    assert [i["label"] for i in created["steps"][1]["items"]] == ["AI Scientific Search (PubMed and indexed literature)"]
    done = client.get(f"/api/v1/runs/{created['id']}/result", headers=h).json()
    assert all(i["status"] == "done" for s in done["steps"] for i in s["items"])
    res = done["result"]
    assert res["relevant"] and res["answer"] and res["follow_ups"]          # suggested by the (simulated) AI service
    assert {s["name"] for s in res["top_sources"]} == set(res["research_summary"]["sources_searched"])
    assert res["research_summary"]["stage_count"] == 6


def test_out_of_scope_question_is_not_researched(client):
    h = auth(client)
    created = run(client, h, module="ai", question="What will the weather be in Paris tomorrow?")
    d = client.get(f"/api/v1/runs/{created['id']}/result", headers=h).json()
    assert d["result"]["relevant"] is False and d["result"]["follow_ups"] == []
    assert [s["status"] for s in d["steps"]][1:] == ["skipped"] * (len(d["steps"]) - 1)


def test_follow_up_thread_and_notify(client):
    h = auth(client)
    first = run(client, h, module="ai", question="Which biomarkers predict response to IRAK4 inhibition?")
    second = run(client, h, module="ai", question="And what about safety?", parent_run_id=first["id"], notify=True)
    assert second["thread_id"] == first["id"] and second["notify"] is True
    thread = client.get(f"/api/v1/runs/{second['id']}/thread", headers=h).json()
    assert [t["id"] for t in thread["turns"]] == [first["id"], second["id"]]
    assert client.post(f"/api/v1/runs/{second['id']}/notify", headers=h, json={"enabled": False}).json()["notify"] is False
    bad = client.post("/api/v1/runs", headers=h, json={"module": "pipeline", "question": "x y", "parent_run_id": first["id"]})
    assert bad.status_code == 422


def test_rephrase(client):
    h = auth(client)
    r = client.post("/api/v1/prompts/rephrase", headers=h, json={"text": "irak4 biomarkers target engagement", "module": "ai"})
    assert r.status_code == 200 and r.json()["rephrased"] != r.json()["original"]     # from the (simulated) AI service
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
    assert res["relevant"] and {a["indication"] for a in res["assets"]} == {"AD"} and res["summary"]


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


def test_refresh_token_rotation_and_logout(client):
    login = client.post("/api/v1/auth/login", json={"email": "swanik@example.com", "password": "ChangeMe123!"}).json()
    access, rt = login["access_token"], login["refresh_token"]
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {rt}"}).status_code == 401   # not an access token
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": access}).status_code == 401   # not a refresh token
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": "garbage"}).status_code == 401
    new = client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
    assert new.status_code == 200 and new.json()["token_type"] == "bearer"
    new = new.json()
    assert new["refresh_token"] != rt and new["access_token"] != access
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {new['access_token']}"}).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": rt}).status_code == 401       # used once only
    # logout with the refresh token: neither token works afterwards
    h = {"Authorization": f"Bearer {new['access_token']}"}
    assert client.post("/api/v1/auth/logout", headers=h, json={"refresh_token": new["refresh_token"]}).status_code == 204
    assert client.get("/api/v1/me", headers=h).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": new["refresh_token"]}).status_code == 401


# ------------------------------------------------------------------ v0.4: normalised roles, groups, pipeline, overrides
def test_roles_groups_admin(client):
    admin = auth(client, "maria.chen@example.com")
    r = client.get("/api/v1/admin/roles", headers=admin).json()
    viewer = next(x for x in r["roles"] if x["name"] == "Viewer")
    assert viewer["permissions"] == ["view"] and len(r["permissions"]) == 7
    g = client.post("/api/v1/admin/groups", headers=admin, json={"name": "Oncology"})
    assert g.status_code == 201
    assert client.post("/api/v1/admin/groups", headers=admin, json={"name": "Oncology"}).status_code == 409
    u = client.post("/api/v1/admin/users", headers=admin,
                    json={"name": "Dee", "email": "dee@example.com", "role": "Viewer", "groups": ["Oncology"], "password": "Pw123456!"})
    assert u.status_code == 201 and u.json()["role"] == "Viewer" and u.json()["groups"] == ["Oncology"]
    assert client.post("/api/v1/admin/users", headers=admin,
                       json={"name": "E", "email": "e@example.com", "role": "Wizard"}).status_code == 422
    # a group with members cannot be deleted
    assert client.delete(f"/api/v1/admin/groups/{g.json()['id']}", headers=admin).status_code == 409
    # permissions now come from the database: granting 'create' to Viewer takes effect immediately
    tok = client.post("/api/v1/auth/login", json={"email": "dee@example.com", "password": "Pw123456!"}).json()["access_token"]
    dee = {"Authorization": f"Bearer {tok}"}
    assert client.post("/api/v1/scoring/assessments", headers=dee, json={"model_id": 1}).status_code == 403
    client.put(f"/api/v1/admin/roles/{viewer['id']}/permissions", headers=admin, json={"permissions": ["view", "create"]})
    assert client.post("/api/v1/scoring/assessments", headers=dee, json={"model_id": 1}).status_code == 201
    client.put(f"/api/v1/admin/roles/{viewer['id']}/permissions", headers=admin, json={"permissions": ["view"]})
    me = next(x for x in r["roles"] if x["name"] == "Admin")
    assert client.put(f"/api/v1/admin/roles/{me['id']}/permissions", headers=admin,
                      json={"permissions": ["view"]}).status_code == 409   # cannot lock yourself out


def test_share_with_group_grants_access(client):
    h = auth(client)                                   # Swanik
    created = run(client, h, module="ai", question="IRAK4 group sharing check")
    item_id = client.post(f"/api/v1/runs/{created['id']}/save", headers=h, json={}).json()["item_id"]
    sam = auth(client, "sam.patel@example.com")        # member of Market Access
    assert client.get(f"/api/v1/workspace/items/{item_id}", headers=sam).status_code == 404
    groups = client.get("/api/v1/users/directory", headers=h).json()["group_options"]
    ma = next(g for g in groups if g["name"] == "Market Access")
    assert client.post(f"/api/v1/workspace/items/{item_id}/share", headers=h,
                       json={"group_id": ma["id"], "permission": "view"}).status_code == 200
    d = client.get(f"/api/v1/workspace/items/{item_id}", headers=sam).json()
    assert d["access"] == "view" and d["shared_with"][0]["group"] == "Market Access"
    assert client.post(f"/api/v1/workspace/items/{item_id}/share", headers=h,
                       json={"group": "No Such Team"}).status_code == 422
    assert client.post(f"/api/v1/workspace/items/{item_id}/share", headers=h,
                       json={"user_id": 3, "group": "Market Access"}).status_code == 422


def test_override_stored_as_row_and_scenario_roundtrip(client):
    h = auth(client)
    a = client.post("/api/v1/scoring/assessments", headers=h, json={"model_id": 1}).json()
    ok = client.put(f"/api/v1/scoring/assessments/{a['id']}/overrides", headers=h,
                    json={"indication": "RA", "criterion": "population", "score": 3, "justification": "Claims data"}).json()
    assert ok["overrides"]["RA"]["population"]["justification"] == "Claims data"
    sid = client.post(f"/api/v1/scoring/assessments/{a['id']}/scenarios", headers=h, json={"name": "S1"}).json()["id"]
    client.post(f"/api/v1/scoring/assessments/{a['id']}/reset", headers=h)
    loaded = client.post(f"/api/v1/scoring/assessments/{a['id']}/scenarios/{sid}/load", headers=h).json()
    assert loaded["overrides"]["RA"]["population"]["score"] == 3


def test_pipeline_options_from_lookups(client):
    o = client.get("/api/v1/pipeline/options", headers=auth(client)).json()
    assert "Sponsor 3" in o["sponsors"] and o["indications"] == ["AD", "RA", "SLE"]
    rows = client.get("/api/v1/pipeline/assets", headers=auth(client), params={"indication": "SLE"}).json()["data"]
    assert {"asset_id", "name", "sponsor", "modality", "target", "indication", "stage"} <= set(rows[0])


def test_history_summary_and_utc_timestamps(client):
    """SQLite drops time zones: summary must not crash once runs exist, and API dates must carry an offset."""
    h = auth(client)
    run(client, h, module="ai", question="IRAK4 timestamp regression")
    s = client.get("/api/v1/history/summary", headers=h)
    assert s.status_code == 200 and s.json()["searches_this_week"] >= 1
    ts = client.get("/api/v1/history/searches", headers=h).json()[0]["created_at"]
    assert ts.endswith("+00:00") or ts.endswith("Z")


def test_stalled_in_progress_run_can_be_resumed(client):
    from datetime import datetime, timedelta, timezone

    from app.db import models as m
    from app.db.base import SessionLocal

    h = auth(client)
    rid = run(client, h, module="ai", question="IRAK4 stalled run check")["id"]
    with SessionLocal() as db:                       # simulate a server restart mid-run
        r = db.get(m.Run, rid)
        r.status, r.step_index = "In Progress", 2
        r.updated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()
    assert client.get(f"/api/v1/runs/{rid}", headers=h).json()["stalled"] is False
    assert client.post(f"/api/v1/runs/{rid}/resume", headers=h).status_code == 409   # may still be running
    with SessionLocal() as db:
        db.get(m.Run, rid).updated_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.commit()
    d = client.get(f"/api/v1/runs/{rid}", headers=h).json()
    assert d["stalled"] and d["resumable"]
    assert client.post(f"/api/v1/runs/{rid}/resume", headers=h).status_code == 202
    assert client.get(f"/api/v1/runs/{rid}", headers=h).json()["status"] == "Completed"
    # the saved progress had no AI answer (stopped mid-run): resuming asked the service again
    assert client.get(f"/api/v1/runs/{rid}/result", headers=h).json()["result"]["answer"]


def test_sample_data_fills_home(client, service):
    """Runs last in this module: it adds sample records to the shared test database."""
    from app.db.base import SessionLocal
    from app.seed_samples import seed_samples

    with SessionLocal() as db:
        assert seed_samples(db) is True
        assert seed_samples(db) is False                 # idempotent
    assert service.calls == []                           # samples never call the billed AI service
    h = auth(client)
    cards = {c["key"]: c["count"] for c in client.get("/api/v1/home/summary", headers=h).json()["cards"]}
    assert cards["in_progress"] >= 6 and cards["saved"] >= 10 and cards["recent_results"] >= 14
    statuses = {r["status"] for r in client.get("/api/v1/home", headers=h, params={"tab": "in_progress"}).json()["items"]}
    assert statuses == {"In Progress", "Paused", "Failed"}
    paused = next(r for r in client.get("/api/v1/home", headers=h, params={"tab": "in_progress"}).json()["items"]
                  if r["status"] == "Paused" and r["module"] == "pipeline")
    assert client.post(f"/api/v1/runs/{paused['id']}/resume", headers=h).status_code == 202
    res = client.get(f"/api/v1/runs/{paused['id']}/result", headers=h).json()["result"]
    assert res["provider"] == "ai_scientific_search" and res["summary"]     # resumed by a user: AI text filled in


def test_admin_edits_with_dates_are_audited(client):
    """Audit snapshots contain dates (last_login, last_sync, updated_at); edits must not fail when storing them."""
    a = auth(client, "maria.chen@example.com")
    sam = next(u for u in client.get("/api/v1/admin/users", headers=a).json() if u["email"] == "sam.patel@example.com")
    assert sam["last_login"]                                   # Sam has logged in during this test module
    r = client.patch(f"/api/v1/admin/users/{sam['id']}", headers=a, json={
        "name": sam["name"], "email": sam["email"], "role": sam["role"], "groups": sam["groups"], "status": "Active"})
    assert r.status_code == 200
    pub = next(c for c in client.get("/api/v1/datasources", headers=a).json()["items"] if c["name"] == "PubMed")
    client.post(f"/api/v1/datasources/{pub['id']}/test", headers=a)   # sets last_sync
    r = client.patch(f"/api/v1/datasources/{pub['id']}", headers=a, json={
        "name": "PubMed", "category": pub["category"], "description": pub["description"], "license": pub["access"]})
    assert r.status_code == 200
    rec = client.get("/api/v1/admin/records/prompt", headers=a).json()[0]
    r = client.patch(f"/api/v1/admin/records/prompt/{rec['id']}", headers=a, json={
        "name": rec["name"], "status": rec["status"], "version": rec["version"], "data": rec["data"]})
    assert r.status_code == 200
    assert client.get("/api/v1/admin/audit", headers=a).json()[0]["after"]["updated_at"]
