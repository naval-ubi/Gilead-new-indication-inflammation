"""Aggregations behind the Pipeline Explorer's five views (table, swimlane, graph, heatmap, radar)
and its KPI tiles. Pure functions over plain dicts so they can be unit-tested and reused by the
report generator."""
from __future__ import annotations

STAGES = ["Research", "Preclinical", "Phase 1", "Phase 2", "Phase 3", "Filed", "Marketed"]
CLINICAL = {"Phase 1", "Phase 2", "Phase 3", "Filed"}
LATE = {"Phase 3", "Filed", "Marketed"}
LIVE = {"Recruiting", "Active"}
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}


def kpis(assets: list[dict]) -> dict:
    return {
        "indications": len({a["indication"] for a in assets}),
        "distinct_targets": len({a["target"] for a in assets}),
        "sponsors": len({a["sponsor"] for a in assets}),
        "clinical_stage_programs": sum(1 for a in assets if a["stage"] in CLINICAL),
    }


def sort_assets(assets: list[dict], key: str = "stage", desc: bool = False) -> list[dict]:
    if key == "stage":
        return sorted(assets, key=lambda a: STAGE_ORDER.get(a["stage"], 99), reverse=desc)
    return sorted(assets, key=lambda a: str(a.get(key, "")).lower(), reverse=desc)


def swimlane(assets: list[dict], indications: list[str]) -> list[dict]:
    return [{"indication": i, "stages": {s: [a for a in assets if a["indication"] == i and a["stage"] == s]
                                         for s in STAGES}} for i in indications]


def stage_counts(assets: list[dict], indications: list[str]) -> list[dict]:
    """Used by both the stacked bar 'graph' view and the heatmap."""
    return [{"stage": s, "counts": {i: sum(1 for a in assets if a["stage"] == s and a["indication"] == i)
                                    for i in indications}} for s in STAGES]


def heatmap(assets: list[dict], indications: list[str]) -> dict:
    grid = {i: {s: sum(1 for a in assets if a["indication"] == i and a["stage"] == s) for s in STAGES}
            for i in indications}
    return {"stages": STAGES, "grid": grid, "max": max([1] + [v for row in grid.values() for v in row.values()])}


def radar(assets: list[dict], indications: list[str]) -> dict:
    dims = ["Assets", "Late-stage", "Active trials", "Modalities", "Sponsors"]
    raw = {}
    for i in indications:
        x = [a for a in assets if a["indication"] == i]
        raw[i] = [len(x), sum(1 for a in x if a["stage"] in LATE), sum(1 for a in x if a["activity"] in LIVE),
                  len({a["modality"] for a in x}), len({a["sponsor"] for a in x})]
    mx = [max([1] + [raw[i][d] for i in indications]) for d in range(len(dims))]
    return {"dimensions": dims, "raw": raw,
            "normalised": {i: [round(v / mx[d], 3) for d, v in enumerate(raw[i])] for i in indications}}
