"""AI Scientific Search service integration, with the service simulated (no network). The response fixture is a
real reply from the service."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import scientific_search as ss
from tests.conftest import REAL, REPHRASED


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def auth(client):
    t = client.post("/api/v1/auth/login", json={"email": "swanik@example.com", "password": "ChangeMe123!"}).json()
    return {"Authorization": f"Bearer {t['access_token']}"}


# ------------------------------------------------------------------ conversion
def test_citations_renumbered_to_reference_numbers():
    numbers = {"S1": 1, "S2": 2, "S3": 3}
    assert ss.renumber_citations("A [S1]. B [S2, S3]. C [S9].", numbers) == "A [1]. B [2, 3]. C [S9]."


def test_real_response_maps_to_result_parts():
    parts = ss.to_result_parts(REAL)
    assert "[S1]" not in parts["answer"] and "[1]" in parts["answer"]
    ref = parts["references"][0]
    assert ref["number"] == 1 and ref["source"] == "PubMed" and ref["url"].startswith("https://pubmed.ncbi.nlm.nih.gov/")
    assert ref["doi"] and ref["journal"]
    assert parts["top_sources"] == [{"name": "PubMed", "url": ref["url"]}]
    assert parts["key_findings"] and all("[" not in f for f in parts["key_findings"])


# ------------------------------------------------------------------ runs
def test_ai_run_uses_service(client, service):
    h = auth(client)
    rid = client.post("/api/v1/runs", headers=h, json={"module": "ai", "question": "What role does IRAK4 play in SLE?"}).json()["id"]
    d = client.get(f"/api/v1/runs/{rid}/result", headers=h).json()
    res = d["result"]
    assert res["provider"] == "ai_scientific_search" and res["relevant"] is True
    assert res["answer"].startswith("# Role of IRAK4") and "[1]" in res["answer"]
    assert res["references"][0]["doi"] and res["research_summary"]["sources_searched"] == ["PubMed"]
    search_stage = next(s for s in d["steps"] if s["name"] == "Search literature")
    assert search_stage["items"] == [{"label": "PubMed", "status": "done"}]
    assert service.calls[-1] == ("POST", "/scientific-search/query", {"query": "What role does IRAK4 play in SLE?"})


def test_out_of_scope_question_does_not_call_service(client, service):
    h = auth(client)
    rid = client.post("/api/v1/runs", headers=h, json={"module": "ai", "question": "What is the weather in Paris?"}).json()["id"]
    assert client.get(f"/api/v1/runs/{rid}/result", headers=h).json()["result"]["relevant"] is False
    assert service.calls == []


def test_service_error_fails_run_and_retry_recovers(client, service):
    h = auth(client)
    service.replies = [(500, {"status": "error", "message": "Model is overloaded, try again"})]
    rid = client.post("/api/v1/runs", headers=h, json={"module": "ai", "question": "IRAK4 biomarkers in RA"}).json()["id"]
    run = client.get(f"/api/v1/runs/{rid}", headers=h).json()
    assert run["status"] == "Failed" and "Model is overloaded" in run["step_label"] and run["resumable"]
    assert client.post(f"/api/v1/runs/{rid}/resume", headers=h).status_code == 202      # service answers now
    assert client.get(f"/api/v1/runs/{rid}", headers=h).json()["status"] == "Completed"


def test_timeout_is_reported_and_not_retried(client, service):
    h = auth(client)
    service.replies = [httpx.ReadTimeout("slow")]
    rid = client.post("/api/v1/runs", headers=h, json={"module": "ai", "question": "IRAK4 degraders in AD"}).json()["id"]
    run = client.get(f"/api/v1/runs/{rid}", headers=h).json()
    assert run["status"] == "Failed" and "did not answer within" in run["step_label"]
    assert len([c for c in service.calls if c[1].endswith("/query")]) == 1


def test_gateway_hiccup_is_retried_once(client, service):
    h = auth(client)
    service.replies = [(503, {"message": "Service Unavailable"})]
    rid = client.post("/api/v1/runs", headers=h, json={"module": "ai", "question": "IRAK4 inhibitors in lupus"}).json()["id"]
    assert client.get(f"/api/v1/runs/{rid}", headers=h).json()["status"] == "Completed"
    assert len([c for c in service.calls if c[1].endswith("/query")]) == 2


# ------------------------------------------------------------------ every module uses the service
def test_pipeline_and_competitive_insights_use_service(client, service):
    h = auth(client)
    rid = client.post("/api/v1/runs", headers=h, json={"module": "pipeline", "question": "",
                                                      "filters": {"indication": "AD"}}).json()["id"]
    res = client.get(f"/api/v1/runs/{rid}/result", headers=h).json()["result"]
    assert res["provider"] == "ai_scientific_search" and res["summary"].startswith("# Role of IRAK4")
    assert {a["indication"] for a in res["assets"]} == {"AD"}             # the table still comes from the database
    assert "AD" in service.queries()[-1]
    service.calls.clear()
    rid = client.post("/api/v1/runs", headers=h, json={"module": "cioa", "question": "Map IRAK4 competitors in SLE",
                                                      "workflow": "competitive", "indication": "SLE"}).json()["id"]
    res = client.get(f"/api/v1/runs/{rid}/result", headers=h).json()["result"]
    assert res["provider"] == "ai_scientific_search" and res["overview"] and res["key_takeaways"]
    assert all(s["text"] and s["references"] for s in res["sections"])
    assert len(service.queries()) == 1 + len(res["sections"])            # the overview plus one per section
    report = client.get(f"/api/v1/runs/{rid}/report", headers=h).json()
    assert report["notice"] is None


def test_report_section_failure_fails_run_and_resume_writes_only_missing(client, service):
    h = auth(client)
    service.replies = [(200, REAL), (200, REAL), (500, {"status": "error", "message": "down"})]
    rid = client.post("/api/v1/runs", headers=h, json={"module": "cioa", "question": "Endpoints used in IRAK4 trials",
                                                      "workflow": "endpoint"}).json()["id"]
    run = client.get(f"/api/v1/runs/{rid}", headers=h).json()
    assert run["status"] == "Failed" and "report sections could not be written" in run["step_label"]
    before = len(service.queries())
    assert client.post(f"/api/v1/runs/{rid}/resume", headers=h).status_code == 202
    res = client.get(f"/api/v1/runs/{rid}/result", headers=h).json()["result"]
    assert all(s["text"] for s in res["sections"])
    assert len(service.queries()) - before == 1                          # only the missing section was asked again


# ------------------------------------------------------------------ not configured: fail clearly
def test_without_service_runs_fail_and_rephrase_is_unavailable(client, no_service):
    h = auth(client)
    for body in ({"module": "ai", "question": "IRAK4 in SLE"}, {"module": "pipeline", "question": "IRAK4 programs in RA"}):
        rid = client.post("/api/v1/runs", headers=h, json=body).json()["id"]
        run = client.get(f"/api/v1/runs/{rid}", headers=h).json()
        assert run["status"] == "Failed" and "not configured" in run["step_label"]
    r = client.post("/api/v1/prompts/rephrase", headers=h, json={"text": "irak4 in lupus", "module": "ai"})
    assert r.status_code == 503
    assert no_service.calls == []


# ------------------------------------------------------------------ rephrase, health, offline
def test_rephrase_uses_service_for_every_module(client, service):
    h = auth(client)
    for module in ("ai", "pipeline", "cioa"):
        r = client.post("/api/v1/prompts/rephrase", headers=h, json={"text": "irak4 role in lupus", "module": module})
        assert r.json()["rephrased"] == REPHRASED
    assert len([c for c in service.calls if c[1].endswith("/rephrase")]) == 3
    service.replies = [(500, {"status": "error", "message": "down"})]
    assert client.post("/api/v1/prompts/rephrase", headers=h, json={"text": "irak4 in lupus", "module": "ai"}).status_code == 503


def test_dependency_health(client, service):
    d = client.get("/health/dependencies").json()["ai_scientific_search"]
    assert d["configured"] and d["ok"]


def test_offline_switches_service_off(service):
    assert not ss.is_offline()
    with ss.offline():
        assert ss.is_offline()
    assert not ss.is_offline()


# ------------------------------------------------------------------ follow-up questions
def test_follow_ups_come_from_service(client, service):
    parts = ss.to_result_parts(REAL)
    assert parts["follow_ups"][1] == "How does IRAK4 inhibition affect TLR7/8 signaling in plasmacytoid dendritic cells in SLE?"
    h = auth(client)
    q = REAL["query"]
    rid = client.post("/api/v1/runs", headers=h, json={"module": "ai", "question": q}).json()["id"]
    res = client.get(f"/api/v1/runs/{rid}/result", headers=h).json()["result"]
    assert res["follow_ups"] and q not in res["follow_ups"]                 # the question just asked is not repeated
    assert res["follow_ups_title"] == "Follow-up questions"
    # a follow-up turn does not suggest a question already asked earlier in the conversation
    nxt = res["follow_ups"][0]
    rid2 = client.post("/api/v1/runs", headers=h, json={"module": "ai", "question": nxt, "parent_run_id": rid}).json()["id"]
    res2 = client.get(f"/api/v1/runs/{rid2}/result", headers=h).json()["result"]
    assert nxt not in res2["follow_ups"] and q not in res2["follow_ups"]


def test_pipeline_answer_has_follow_ups(client, service):
    h = auth(client)
    rid = client.post("/api/v1/runs", headers=h, json={"module": "pipeline", "question": "IRAK4 programs in SLE"}).json()["id"]
    assert client.get(f"/api/v1/runs/{rid}/result", headers=h).json()["result"]["follow_ups"]


def test_reply_without_follow_ups_still_works(client, service):
    service.replies = [(200, {k: v for k, v in REAL.items() if k != "follow_up_questions"})]
    h = auth(client)
    rid = client.post("/api/v1/runs", headers=h, json={"module": "ai", "question": "IRAK4 and TLR signalling"}).json()["id"]
    res = client.get(f"/api/v1/runs/{rid}/result", headers=h).json()["result"]
    assert res["answer"] and res["follow_ups"] == [] and res["follow_ups_title"] is None
