"""Pure-logic tests: run with `pytest tests/test_scoring.py` (no DB or FastAPI needed)."""
from app.services import pipeline as pl
from app.services import scoring as sc

KEYS = ["pop", "unmet", "head", "payer", "price"]
W = {k: 20 for k in KEYS}
AI = {"SLE": dict(zip(KEYS, [3, 5, 4, 3, 4])), "RA": dict(zip(KEYS, [5, 2, 1, 4, 2])), "AD": dict(zip(KEYS, [5, 3, 2, 3, 2]))}
INDS = ["SLE", "RA", "AD"]


def test_equal_weights_ranking_matches_prototype():
    res = {r.indication: r for r in sc.calculate(INDS, KEYS, W, AI)}
    assert res["SLE"].norm == 76.0 and res["RA"].norm == 56.0 and res["AD"].norm == 60.0
    assert sc.ranking(list(res.values())) == {"SLE": 1, "AD": 2, "RA": 3}
    assert res["SLE"].priority == "Medium" and res["RA"].priority == "Low"


def test_no_result_unless_weights_total_100():
    bad = {**W, "pop": 30}
    assert sc.validate_weights(bad, KEYS)
    assert all(r.norm is None for r in sc.calculate(INDS, KEYS, bad, AI))


def test_missing_score_blocks_result():
    ai = {**AI, "SLE": {**AI["SLE"], "price": None}}
    r = {x.indication: x for x in sc.calculate(INDS, KEYS, W, ai)}
    assert r["SLE"].norm is None and r["SLE"].missing == 1 and r["RA"].norm is not None


def test_override_wins_over_ai():
    ov = {"RA": {"pop": {"score": 1, "justification": "x"}}}
    before = {r.indication: r.norm for r in sc.calculate(INDS, KEYS, W, AI)}
    after = {r.indication: r.norm for r in sc.calculate(INDS, KEYS, W, AI, ov)}
    assert after["RA"] < before["RA"] and after["SLE"] == before["SLE"]


def test_sensitivity_rescales_to_100_and_reports_delta():
    out = sc.sensitivity(INDS, KEYS, W, AI)
    assert out["base"]["SLE"] == 1 and len(out["scenarios"]) == 5
    # doubling "unmet" favours SLE (score 5) over RA (score 2): RA must not improve
    unmet = next(s for s in out["scenarios"] if s["criterion"] == "unmet")
    assert unmet["ranks"]["RA"]["rank"] >= out["base"]["RA"]


ASSETS = [
    {"indication": "SLE", "target": "t1", "sponsor": "s1", "modality": "a", "stage": "Phase 2", "activity": "Recruiting"},
    {"indication": "SLE", "target": "t1", "sponsor": "s2", "modality": "b", "stage": "Phase 3", "activity": "Active"},
    {"indication": "RA", "target": "t2", "sponsor": "s1", "modality": "a", "stage": "Preclinical", "activity": "—"},
]


def test_pipeline_kpis_heatmap_radar():
    k = pl.kpis(ASSETS)
    assert k == {"indications": 2, "distinct_targets": 2, "sponsors": 2, "clinical_stage_programs": 2}
    h = pl.heatmap(ASSETS, ["SLE", "RA"])
    assert h["grid"]["SLE"]["Phase 2"] == 1 and h["max"] == 1
    r = pl.radar(ASSETS, ["SLE", "RA"])
    assert r["raw"]["SLE"] == [2, 1, 2, 2, 2] and max(r["normalised"]["SLE"]) == 1.0
    assert [a["stage"] for a in pl.sort_assets(ASSETS)] == ["Preclinical", "Phase 2", "Phase 3"]
