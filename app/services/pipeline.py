"""Aggregations behind the Pipeline Explorer's five views (table, swimlane, graph, heatmap, radar)
and its KPI tiles. Pure functions over plain dicts so they can be unit-tested and reused by the
report generator."""
from __future__ import annotations

import re

STAGES = ["Discovery", "Preclinical", "Phase 1", "Phase 2", "Phase 3", "Filed", "Marketed"]
# Stage filter values in the UI that map onto a canonical stage (and legacy stored values)
STAGE_ALIASES = {"Research": "Discovery", "Regulatory review": "Filed", "Approved": "Marketed"}
# "Development status" options of the stage filter match on trial activity rather than stage
STATUS_FILTERS = {"On hold": r"hold", "Discontinued": r"terminat|discontinu|withdrawn"}
PHASE_FILTERS = {"ep1": "Phase 1", "p1": "Phase 1", "p2": "Phase 2", "p3": "Phase 3", "p4": "Marketed"}
CLINICAL = {"Phase 1", "Phase 2", "Phase 3", "Filed"}
LATE = {"Phase 3", "Filed", "Marketed"}
LIVE = {"Recruiting", "Active"}
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}


def canonical_stage(stage: str) -> str:
    return STAGE_ALIASES.get(stage, stage)


def normalise(asset: dict) -> dict:
    return {**asset, "stage": canonical_stage(asset.get("stage", ""))}


def stage_matches(asset: dict, wanted: str | None) -> bool:
    if not wanted:
        return True
    if wanted in STATUS_FILTERS:
        return bool(re.search(STATUS_FILTERS[wanted], asset.get("activity") or "", re.I))
    return asset["stage"] == canonical_stage(wanted)


def filter_assets(assets: list[dict], filters: dict | None = None, more: dict | None = None) -> list[dict]:
    """The single filter used by the run builder, the live /pipeline/assets endpoint and per-turn filters.
    `more` is the 'More filters' dialog: phase and recruiting status apply to programs here; the remaining
    trial-level filters (sex, age, study type, results, documents, funder) are passed to trial connectors."""
    f, more = filters or {}, more or {}
    out = []
    for a in map(normalise, assets):
        if f.get("target") and a["target"] != f["target"]:
            continue
        if f.get("indication") and a["indication"] != f["indication"]:
            continue
        if f.get("modality") and a["modality"] != f["modality"]:
            continue
        if not stage_matches(a, f.get("stage")):
            continue
        phases = {PHASE_FILTERS[p] for p in more.get("phase") or [] if p in PHASE_FILTERS}
        if phases and a["stage"] not in phases:
            continue
        if more.get("status") == "rec" and a.get("activity") not in ("Recruiting", "Not yet recruiting"):
            continue
        out.append(a)
    return out


def kpi_tiles(assets: list[dict]) -> list[dict]:
    """KPI row with ready-to-render labels (singular/plural) and icon keys, as the UI shows them."""
    k = kpis(assets)
    plural = lambda n, one, many: one if n == 1 else many  # noqa: E731
    return [
        {"key": "indications", "icon": "net", "value": k["indications"], "label": plural(k["indications"], "Indication", "Indications")},
        {"key": "distinct_targets", "icon": "target", "value": k["distinct_targets"],
         "label": plural(k["distinct_targets"], "Distinct Target", "Distinct Targets")},
        {"key": "sponsors", "icon": "users", "value": k["sponsors"], "label": plural(k["sponsors"], "Sponsor", "Sponsors")},
        {"key": "clinical_stage_programs", "icon": "pulse", "value": k["clinical_stage_programs"], "label": "Clinical-Stage Programs"},
    ]


def kpis(assets: list[dict]) -> dict:
    return {
        "indications": len({a["indication"] for a in assets}),
        "distinct_targets": len({a["target"] for a in assets}),
        "sponsors": len({a["sponsor"] for a in assets}),
        "clinical_stage_programs": sum(1 for a in assets if canonical_stage(a["stage"]) in CLINICAL or a["stage"] == "Marketed"),
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
