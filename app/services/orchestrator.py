"""Research run orchestrator.

A Run is a list of named steps executed in order; each step has sub-items (the lines the research loader shows
under a stage, e.g. one item per data source in "Search literature"). State (status, step_index, item statuses,
activity feed, emerging findings, partial output) is persisted as work progresses, which gives us:
  * the staged loader: stages, per-source "Searching / Searched", "Live activity", "Emerging findings"
    (poll GET /runs/{id} or stream /runs/{id}/events)
  * Cancel / Pause (status flipped by the API; checked between steps)
  * Resume (re-enter at step_index; History "Resume" button)
  * Failure isolation ("Failed at step 2 - Citeline connector timed out")

Execution uses FastAPI BackgroundTasks (in-process). For production move `execute_run` to a worker
(Arq / Celery / RQ) - nothing else changes because the function only needs a run id.
"""
from __future__ import annotations

import traceback
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import models as m
from app.db.base import SessionLocal
from app.services import catalog
from app.services import pipeline as pl
from app.services.integrations import fan_out, get_llm, in_scope, suggest_follow_ups

# Legacy CIOA capability list (GET /cioa/capabilities) = subcategories of the default workflow.
CAPABILITIES = [(n, "") for n in catalog.WORKFLOWS[0]["subcategories"]]
ACTIVITY_KEEP = 30


# ------------------------------------------------------------------ planning
def resolve_sources(db, module: str, chosen: list[str] | None) -> list[str]:
    """Sources a run will search: the user's subset, else the module defaults; the admin switch always wins."""
    enabled = [c.name for c in db.scalars(select(m.Connector).where(m.Connector.enabled.is_(True)))]
    if chosen:
        return [n for n in enabled if n in chosen]
    defaults = [n for n in catalog.DEFAULT_SOURCES.get(module, []) if n in enabled]
    return defaults or enabled


def _step(name: str, items: list[str]) -> dict:
    return {"name": name, "status": "pending", "items": [{"label": i, "status": "pending"} for i in items]}


def plan_steps(run: m.Run) -> list[dict]:
    p = run.params or {}
    ctx = p.get("context", {})
    target = ctx.get("target", "IRAK4")
    inds = ", ".join(p.get("indications") or ctx.get("indications") or []) or "the selected indications"
    sources = p.get("resolved_sources") or []
    if run.module == "ai":
        steps = [
            _step("Interpret question", [f"Reading research context: {target}", f"Identifying indications: {inds}",
                                         "Classifying the question as scientific evidence review"]),
            _step("Search literature", sources),
            _step("Evaluate clinical evidence", ["Scoring evidence relevance", "Separating verified evidence from assumptions"]),
            _step("Compare indications", [f"Comparing evidence across {inds}", "Checking where findings are shared or disease-specific"]),
            _step("Draft answer", ["Writing an evidence-backed answer", "Linking citations to each claim"]),
        ]
        if run.mode == "Deep Research":
            steps.insert(3, _step("Cross-check findings", ["Following up on gaps", "Cross-checking conflicting sources"]))
        return steps
    if run.module == "pipeline":
        f = {k: v for k, v in (p.get("filters") or {}).items() if v}
        flt = ("Applying filters: " + ", ".join(f"{k.title()}: {v}" for k, v in f.items())) if f else \
            "No filters applied: covering all targets, indications and stages"
        return [
            _step("Interpret question", [f"Reading research context: {target}", flt, "Expanding synonyms and alternate drug names"]),
            _step("Search pipeline sources", sources),
            _step("Normalise assets", ["Merging duplicate assets", "Matching sponsors"]),
            _step("Classify stages", ["Assigning development stage to each program", "Flagging on-hold and discontinued programs"]),
            _step("Build views and answer", ["Building table, swimlane, graph, heatmap and radar views",
                                             "Writing an evidence-backed summary"]),
        ]
    if run.module == "cioa":
        w = catalog.resolve_workflow(run.workflow or p.get("workflow"))
        subs = w["subcategories"]
        if p.get("capabilities") and w is catalog.WORKFLOWS[0]:  # legacy: subset of capabilities
            subs = [subs[i] for i in p["capabilities"] if 0 <= i < len(subs)] or subs
        mt = p.get("molecular_target") or target
        return [
            _step("Interpret question", [f"Reading context: {mt} in {inds}", f"Matching your question to the {w['name']} workflow"]),
            _step("Search sources", sources),
            _step(f"Run {w['short']} analysis", subs),
            _step("Assess competitive evidence", ["Comparing assets, sponsors and mechanisms", "Ranking threats and opportunities",
                                                  "Checking evidence gaps"]),
            _step("Draft report", ["Writing key takeaways", f"Assembling {len(w['sections'])} report sections"]),
        ]
    raise ValueError(f"unknown module {run.module}")


# ------------------------------------------------------------------ persistence helpers
def _set(db, run: m.Run, **kw):
    for k, v in kw.items():
        setattr(run, k, v)
    db.commit()


def _persist(db, run: m.Run, steps: list[dict], work: dict, **kw) -> None:
    work["activity"] = work.get("activity", [])[-ACTIVITY_KEEP:]
    _set(db, run, steps=[{**s, "items": [dict(i) for i in s.get("items", [])]} for s in steps], result={"_work": work}, **kw)


def _log(work: dict, line: str) -> None:
    work.setdefault("activity", []).append(line)


def _walk_items(db, run, steps, idx, work) -> None:
    """Default item progression for steps whose work is a single unit: tick each sub-item off in order."""
    for it in steps[idx].get("items", []):
        if it["status"] == "done":
            continue
        it["status"] = "active"
        _persist(db, run, steps, work)
        it["status"] = "done"
        _log(work, it["label"])
    _persist(db, run, steps, work)


def _thread_questions(db, run: m.Run) -> list[str]:
    if not run.thread_id:
        return []
    rows = db.scalars(select(m.Run).where(m.Run.thread_id == run.thread_id, m.Run.id != run.id,
                                          m.Run.deleted.is_(False))).all()
    return [r.question for r in rows]


def elapsed_seconds(run: m.Run) -> int:
    def aware(d):
        return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)
    start = aware(run.created_at)
    end = aware(run.updated_at) if run.status in ("Completed", "Failed", "Cancelled") else datetime.now(timezone.utc)
    return max(0, int(((end or start) - start).total_seconds())) if start else 0


# ------------------------------------------------------------------ step dispatch
def _run_step(db, run: m.Run, idx: int, steps: list[dict], work: dict) -> None:
    ctx = run.params.get("context", {})
    name = steps[idx]["name"]
    if idx == 0:
        _walk_items(db, run, steps, idx, work)
        if run.module in catalog.SCOPE and not in_scope(run.question, run.module, ctx):
            work["relevant"] = False
        return
    if name in ("Search literature", "Search pipeline sources", "Search sources"):
        items = {i["label"]: i for i in steps[idx]["items"]}

        def progress(source: str, status: str) -> None:
            if source in items:
                items[source]["status"] = status
                if status != "active":
                    _log(work, f"{'Searched' if status == 'done' else 'Could not reach'} {source}")
                _persist(db, run, steps, work)

        records, errors = fan_out(run.question, ctx, [i["label"] for i in steps[idx]["items"]], progress)
        work["records"], work["errors"] = records, errors
        work["sources_searched"] = [i["label"] for i in steps[idx]["items"] if i["status"] == "done"]
        return
    if name == "Evaluate clinical evidence":
        work["findings"] = [{"text": r.get("snippet") or r.get("title", ""), "source": r.get("source"),
                             "evidence": catalog.evidence_kind(f"{r.get('title', '')} {r.get('snippet', '')}"),
                             "preliminary": True} for r in work.get("records", [])][:3]
    _walk_items(db, run, steps, idx, work)
    if name == "Draft answer":
        work["answer"] = build_ai_result(db, run, work)
    elif name == "Build views and answer":
        work["answer"] = build_pipeline_result(db, run, work)
    elif name == "Draft report":
        work["answer"] = build_cioa_result(db, run, work)


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
            steps = [{**s, "items": [dict(x) for x in s.get("items", [])]} for s in run.steps]
            steps[i]["status"] = "active"
            _persist(db, run, steps, work, step_index=i,
                     step_label=f"Step {i + 1} of {len(steps)} - {steps[i]['name']}")
            try:
                _run_step(db, run, i, steps, work)
            except Exception as e:  # noqa: BLE001
                steps[i]["status"] = "failed"
                _set(db, run, steps=steps, status="Failed", error=f"{e}", step_label=f"Failed at step {i + 1} - {e}",
                     result={"_work": work, "trace": traceback.format_exc()[-1500:]})
                notify(db, run, "Research failed", f"{run.title[:70]} failed at step {i + 1}")
                return
            steps[i]["status"] = "done"
            _persist(db, run, steps, work)
            i += 1
            if work.get("relevant") is False:  # out of scope: skip the research, answer with guidance instead
                for s in steps[i:]:
                    s["status"] = "skipped"
                _persist(db, run, steps, work)
                break
        if work.get("relevant") is False:
            final = not_relevant_result(db, run)
        else:
            final = work.get("answer") or {}
            final["sources_used"] = sorted({r["source"] for r in work.get("records", [])})
            final["connector_errors"] = work.get("errors", [])
        db.refresh(run)
        final["research_summary"] = {"stage_count": len(run.steps), "stages": [s["name"] for s in run.steps],
                                     "sources_searched": work.get("sources_searched", []),
                                     "elapsed_seconds": elapsed_seconds(run)}
        _set(db, run, status="Completed", step_index=len(run.steps), result=final,
             source_count=len(work.get("records", [])) or run.source_count, step_label="Completed")
        if final.get("relevant", True):
            done = {"cioa": f"{catalog.resolve_workflow(run.workflow)['short']} report is ready to review",
                    "pipeline": f"Pipeline results are ready: {run.title[:70]}"}.get(run.module,
                                                                                      f"Your answer is ready: {run.title[:70]}")
            notify(db, run, "Research completed", done)
    finally:
        db.close()


def notify(db, run: m.Run, title: str, body: str) -> None:
    """In-app notification. Sent when the user keeps in-app notifications on, or explicitly asked
    'Notify me when ready' on this run (an explicit opt-in overrides the global preference)."""
    u = db.get(m.User, run.user_id)
    if u and (u.prefs.get("in_app_notifications", True) or run.notify):
        db.add(m.Notification(user_id=run.user_id, title=title, body=body, route=run.module))
        db.commit()


# ------------------------------------------------------------------ result builders (replace stubs with LLM/agents)
def not_relevant_result(db, run: m.Run) -> dict:
    return {"relevant": False,
            "message": f"Please ask a relevant question. I can answer {catalog.SCOPE.get(run.module, 'questions in this module')}. "
                       "I could not match your question to that scope, so I have not generated an answer.",
            "follow_ups": suggest_follow_ups(run.question, run.module, _thread_questions(db, run)),
            "follow_ups_title": "Questions you can ask"}


def _references(records: list[dict]) -> tuple[list[dict], list[dict]]:
    refs = [{"title": r.get("title"), "url": r.get("url"), "source": r.get("source"), "note": r.get("snippet")} for r in records]
    seen, top = set(), []
    for r in records:
        if r["source"] not in seen:
            seen.add(r["source"])
            top.append({"name": r["source"], "url": r.get("url")})
    return refs, top


def build_ai_result(db, run: m.Run, work: dict) -> dict:
    ctx = run.params.get("context", {})
    inds = ctx.get("indications") or ["SLE", "RA", "AD"]
    target = ctx.get("target", "IRAK4")
    records = work.get("records", [])
    refs, top = _references(records)
    answer = get_llm().complete("You are a pharma research analyst. Answer with headed paragraphs and cite each claim.",
                                run.question)
    return {
        "relevant": True,
        "answer": answer,                 # markdown/HTML body rendered in the conversation
        "answer_format": "markdown",
        "summary": answer,                # kept for clients built against v0.1
        "key_findings": [f"Target biology links {target} to innate-immune signalling across {', '.join(inds)}.",
                         "Unmet need persists in patients refractory to standard of care."],
        "top_sources": top,               # "View Details" drawer
        "references": refs,               # numbered reference list under the answer
        "follow_ups": suggest_follow_ups(run.question, "ai", _thread_questions(db, run)),
        "follow_ups_title": "Follow-up questions",
        "dimensions": [{"dimension": d, "ratings": {i: "Medium" for i in inds}} for d in
                       ["Disease burden", "Unmet need", "Competitive intensity", "Evidence strength", "Payer complexity"]],
        "evidence": records, "clinical_trials": [], "assets": [],
        "evidence_quality": {"verified": len(records), "assumptions": 0},
    }


def query_assets(db, filters: dict | None, more: dict | None) -> list[dict]:
    rows = [p.as_row() for p in db.scalars(select(m.PipelineProgram).order_by(m.PipelineProgram.id)).unique()]
    return pl.filter_assets(rows, filters, more)


def pipeline_views(assets: list[dict], inds: list[str], sort: str = "stage", desc: bool = False) -> dict:
    return {"assets": pl.sort_assets(assets, sort, desc), "kpis": pl.kpis(assets), "kpi_tiles": pl.kpi_tiles(assets),
            "swimlane": pl.swimlane(assets, inds), "stage_counts": pl.stage_counts(assets, inds),
            "heatmap": pl.heatmap(assets, inds), "radar": pl.radar(assets, inds), "total": len(assets)}


def build_pipeline_result(db, run: m.Run, work: dict | None = None) -> dict:
    f = run.params.get("filters") or {}
    more = run.params.get("more_filters") or {}
    assets = query_assets(db, f, more)
    inds = [f["indication"]] if f.get("indication") else run.params.get("context", {}).get("indications") or ["SLE", "RA", "AD"]
    refs, top = _references((work or {}).get("records", []))
    summary = get_llm().complete("You are a drug pipeline analyst. Summarise the programs and cite sources.", run.question)
    return {"relevant": True, "summary": summary, "answer_format": "markdown",
            "filters": f, "more_filters": more, "more_filters_count": catalog.more_filters_count(more),
            **pipeline_views(assets, inds), "top_sources": top, "references": refs,
            "follow_ups": suggest_follow_ups(run.question, "pipeline", _thread_questions(db, run)),
            "follow_ups_title": "Follow-up questions"}


def build_cioa_result(db, run: m.Run, work: dict) -> dict:
    p = run.params
    w = catalog.resolve_workflow(run.workflow or p.get("workflow"))
    ctx = p.get("context", {})
    inds = p.get("indications") or ctx.get("indications") or []
    mt = p.get("molecular_target") or ctx.get("target", "IRAK4")
    llm = get_llm()
    assets = [a for a in query_assets(db, {}, {}) if not inds or a["indication"] in inds]
    stats = [
        {"value": len(assets), "label": "Programs tracked", "icon": "capsule"},
        {"value": sum(1 for a in assets if a.get("activity") in pl.LIVE), "label": "Active in the clinic", "icon": "pulse"},
        {"value": len({a["sponsor"] for a in assets}), "label": "Sponsors", "icon": "users"},
    ]
    sections = [{"index": i, "title": t,
                 "bullets": [llm.complete(f"You are writing the '{t}' section of a {w['name']} report. Be concise; cite sources.",
                                          run.question)],
                 "table": None} for i, t in enumerate(w["sections"])]
    return {
        "relevant": True,
        "workflow": {"key": w["key"], "name": w["name"], "short": w["short"], "icon": w["icon"]},
        "context": {"therapeutic_area": p.get("therapeutic_area") or catalog.DEFAULT_THERAPEUTIC_AREA,
                    "indications": inds, "molecular_target": mt},
        "stats": stats,
        "key_takeaways": [llm.complete(f"Key takeaways of a {w['name']} report", run.question)],
        "sections": sections,
        "data_sources": w["sources"],
        "illustrative": True,
    }
