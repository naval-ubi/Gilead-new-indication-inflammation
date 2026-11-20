"""Research run orchestrator.

A Run is a list of named steps executed in order. State (status, step_index, partial output) is
persisted after every step, which gives us for free:
  * progress for the loader UI (poll GET /runs/{id} or stream /runs/{id}/events)
  * Cancel   (status flipped by the API; checked between steps)
  * Pause    (same mechanism)
  * Resume   (re-enter at step_index; History "Resume" button)
  * Failure isolation ("Failed at step 2 - Citeline connector timed out")

Execution here uses FastAPI BackgroundTasks (in-process). For production move `execute_run` to a
worker (Arq / Celery / RQ) - nothing else changes because the function only needs a run id.
"""
from __future__ import annotations

import traceback

from sqlalchemy import select

from app.db import models as m
from app.db.base import SessionLocal
from app.services import pipeline as pl
from app.services.integrations import fan_out, get_llm

CAPABILITIES = [  # CIOA agents (one report section each)
    ("Define Market & Therapeutic Focus", "Scope target, indications, geography and horizon."),
    ("Discover Competing Assets & Therapies", "Inventory approved, investigational and emerging assets."),
    ("Assess Clinical Pipeline & IP Landscape", "Trials, endpoints, milestones and patent families."),
    ("Evaluate Market Dynamics & Demand", "Disease burden, treatment landscape and market size."),
    ("Assess Payer & Access Landscape", "Coverage, restrictions and reimbursement barriers."),
    ("Benchmark Pricing", "Annual WAC benchmarks for comparable therapies."),
    ("Rank Competitive Threats", "Threat ranking of competing programmes."),
    ("Identify Whitespace Opportunities", "Unmet need vs competitive headroom."),
    ("Synthesize Strategic Implications", "Implications for positioning and sequencing."),
    ("Compile Competitive Opportunity Report", "Assemble sections, key takeaways and references."),
]


def plan_steps(run: m.Run) -> list[dict]:
    if run.module == "ai":
        names = ["Interpreting your question", "Searching selected data sources", "Extracting and ranking evidence"]
        if run.mode == "Deep Research":
            names.append("Deep Research: cross-checking findings")
        names.append("Synthesising answer")
    elif run.module == "pipeline":
        names = ["Interpreting your question", "Querying data sources", "Normalising assets and sponsors", "Building views"]
    elif run.module == "cioa":
        sel = run.params.get("capabilities") or list(range(len(CAPABILITIES)))
        names = [CAPABILITIES[i][0] for i in sel]
    else:
        raise ValueError(f"unknown module {run.module}")
    return [{"name": n, "status": "pending"} for n in names]


def _set(db, run: m.Run, **kw):
    for k, v in kw.items():
        setattr(run, k, v)
    db.commit()


def _enabled_sources(db, run: m.Run) -> list[str]:
    chosen = run.params.get("sources")  # user picked a subset in "Data sources for this search"
    q = select(m.Connector).where(m.Connector.enabled.is_(True))  # admin switch always wins
    names = [c.name for c in db.scalars(q)]
    return [n for n in names if not chosen or n in chosen]


def _step(db, run: m.Run, idx: int, work: dict) -> None:
    """Dispatch one step. Real implementations call fan_out()/get_llm(); the stubs keep the API runnable."""
    ctx = run.params.get("context", {})
    name = run.steps[idx]["name"]
    if run.module == "ai":
        if idx == 1:
            records, errors = fan_out(run.question, ctx, _enabled_sources(db, run))
            work["records"], work["errors"] = records, errors
        elif name.startswith("Synthesising"):
            work["answer"] = build_ai_result(run, work)
    elif run.module == "pipeline":
        if idx == 1:
            _, work["errors"] = fan_out(run.question, ctx, _enabled_sources(db, run))
        elif name == "Building views":
            work["answer"] = build_pipeline_result(db, run)
    elif run.module == "cioa":
        work.setdefault("sections", []).append(build_cioa_section(run, idx))


def execute_run(run_id: int, start_at: int | None = None) -> None:
    db = SessionLocal()
    try:
        run = db.get(m.Run, run_id)
        if not run:
            return
        if not run.steps:
            run.steps = plan_steps(run)
        i = run.step_index if start_at is None else start_at
        work = dict((run.result or {}).get("_work", {}))
        _set(db, run, status="In Progress", error=None)
        while i < len(run.steps):
            db.refresh(run)
            if run.status in ("Cancelled", "Paused"):
                return  # API flipped status; stop cleanly between steps
            steps = [dict(s) for s in run.steps]
            steps[i]["status"] = "active"
            _set(db, run, steps=steps, step_index=i, step_label=f"Step {i + 1} of {len(steps)} - {steps[i]['name']}")
            try:
                _step(db, run, i, work)
            except Exception as e:  # noqa: BLE001
                steps[i]["status"] = "failed"
                _set(db, run, steps=steps, status="Failed", error=f"{e}", step_label=f"Failed at step {i + 1} - {e}",
                     result={"_work": work, "trace": traceback.format_exc()[-1500:]})
                notify(db, run.user_id, "Research failed", f"{run.title[:70]} failed at step {i + 1}", run.module)
                return
            steps[i]["status"] = "done"
            _set(db, run, steps=steps, result={"_work": work})
            i += 1
        final = work.get("answer") or {"sections": work.get("sections", [])}
        final["sources_used"] = sorted({r["source"] for r in work.get("records", [])})
        final["connector_errors"] = work.get("errors", [])
        _set(db, run, status="Completed", step_index=len(run.steps), result=final,
             source_count=len(work.get("records", [])) or run.source_count,
             step_label="Completed")
        notify(db, run.user_id, "Research completed", f"Your result is ready: {run.title[:70]}", run.module)
    finally:
        db.close()


def notify(db, user_id: int, title: str, body: str, route: str) -> None:
    u = db.get(m.User, user_id)
    if u and u.prefs.get("in_app_notifications", True):
        db.add(m.Notification(user_id=user_id, title=title, body=body, route=route))
        db.commit()


# ------------------------------------------------------------------ result builders (replace stubs with LLM/agents)
def build_ai_result(run: m.Run, work: dict) -> dict:
    ctx = run.params.get("context", {})
    inds = ctx.get("indications") or ["SLE", "RA", "AD"]
    target = ctx.get("target", "IRAK4")
    records = work.get("records", [])
    summary = get_llm().complete("You are a pharma strategy analyst. Cite evidence.", run.question)
    return {
        "summary": summary,
        "key_findings": [f"Target biology links {target} to innate-immune signalling across {', '.join(inds)}.",
                         "Unmet need persists in patients refractory to standard of care."],
        "dimensions": [{"dimension": d, "ratings": {i: "Medium" for i in inds}} for d in
                       ["Disease burden", "Unmet need", "Competitive intensity", "Evidence strength", "Payer complexity"]],
        "evidence": records, "clinical_trials": [], "assets": [],
        "top_sources": sorted({r["source"] for r in records})[:4],
        "evidence_quality": {"verified": len(records), "assumptions": 0},
    }


def build_pipeline_result(db, run: m.Run) -> dict:
    f = run.params.get("filters", {})
    q = select(m.PipelineAsset)
    for col, key in [("target", "target"), ("indication", "indication"), ("modality", "modality"), ("stage", "stage")]:
        if f.get(key):
            q = q.where(getattr(m.PipelineAsset, col) == f[key])
    assets = [{c.name: getattr(a, c.name) for c in a.__table__.columns if c.name != "updated_at"} for a in db.scalars(q)]
    inds = [f["indication"]] if f.get("indication") else run.params.get("context", {}).get("indications") or ["SLE", "RA", "AD"]
    return {"assets": pl.sort_assets(assets), "kpis": pl.kpis(assets), "swimlane": pl.swimlane(assets, inds),
            "stage_counts": pl.stage_counts(assets, inds), "heatmap": pl.heatmap(assets, inds), "radar": pl.radar(assets, inds)}


def build_cioa_section(run: m.Run, idx: int) -> dict:
    sel = run.params.get("capabilities") or list(range(len(CAPABILITIES)))
    title, desc = CAPABILITIES[sel[idx]]
    return {"index": idx, "title": title, "description": desc,
            "key_takeaways": [], "table": [], "references": []}  # TODO: one agent per capability
