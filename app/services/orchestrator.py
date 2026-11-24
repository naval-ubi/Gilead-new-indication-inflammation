"""Research run orchestrator.

A Run is a list of named steps executed in order; each step has sub-items (the lines the research loader shows
under a stage). State (status, step_index, item statuses, activity feed, emerging findings, partial output) is
persisted as work progresses, which gives us:
  * the staged loader: stages, sources searched, "Live activity", "Emerging findings"
    (poll GET /runs/{id} or stream /runs/{id}/events)
  * Cancel / Pause (status flipped by the API; checked between steps)
  * Resume (re-enter at step_index; History "Resume" button)
  * Failure isolation ("Failed at step 2 - The scientific search service did not answer within 90 seconds")

All AI-written content (AI Search answers, pipeline summaries, Competitive Insights takeaways and sections) comes
from the AI Scientific Search service. Counts, tables and charts come from the database. Nothing is made up: when
the service is not configured, runs fail with a clear message. Sample data (app/seed_samples.py) runs with
`ai_offline` and simply has no AI text.

Execution uses FastAPI BackgroundTasks (in-process). For production move `execute_run` to a worker
(Arq / Celery / RQ) - nothing else changes because the function only needs a run id.
"""
from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import models as m
from app.db.base import SessionLocal
from app.services import catalog, scientific_search
from app.services import pipeline as pl
from app.services.integrations import in_scope

# Legacy CIOA capability list (GET /cioa/capabilities) = subcategories of the default workflow.
CAPABILITIES = [(n, "") for n in catalog.WORKFLOWS[0]["subcategories"]]
ACTIVITY_KEEP = 30
SERVICE_SOURCE = "AI Scientific Search (PubMed and indexed literature)"
SEARCH_STEPS = ("Search literature", "Search pipeline sources", "Search sources")
SECTION_WORKERS = 3   # report sections asked in parallel (each takes ~10-15 s)


# ------------------------------------------------------------------ planning
def _step(name: str, items: list[str]) -> dict:
    return {"name": name, "status": "pending", "items": [{"label": i, "status": "pending"} for i in items]}


def plan_steps(run: m.Run) -> list[dict]:
    p = run.params or {}
    ctx = p.get("context", {})
    target = ctx.get("target", "IRAK4")
    inds = ", ".join(p.get("indications") or ctx.get("indications") or []) or "the selected indications"
    search = ["Pipeline database"] if p.get("ai_offline") else [SERVICE_SOURCE]
    if run.module == "ai":
        steps = [
            _step("Interpret question", [f"Reading research context: {target}", f"Identifying indications: {inds}",
                                         "Classifying the question as scientific evidence review"]),
            _step("Search literature", search),
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
            _step("Search pipeline sources", search),
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
            _step("Search sources", search),
            _step(f"Run {w['short']} analysis", subs),
            _step("Assess competitive evidence", ["Comparing assets, sponsors and mechanisms", "Ranking threats and opportunities",
                                                  "Checking evidence gaps"]),
            _step("Draft report", ["Writing key takeaways", f"Writing {len(w['sections'])} report sections"]),
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


# An "In Progress" run whose state has not changed for this long has no worker behind it any more (e.g. the server
# restarted mid-run). It is reported as stalled and can be resumed. The engine saves progress after every sub-item,
# so a healthy run never stays silent this long.
STALL_AFTER = timedelta(minutes=15)


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def is_stalled(run: m.Run) -> bool:
    return run.status == "In Progress" and run.updated_at is not None and \
        datetime.now(timezone.utc) - _aware(run.updated_at) > STALL_AFTER


def elapsed_seconds(run: m.Run) -> int:
    start = _aware(run.created_at)
    stopped = run.status in ("Completed", "Failed", "Cancelled", "Paused") or is_stalled(run)
    end = _aware(run.updated_at) if stopped else datetime.now(timezone.utc)
    return max(0, int(((end or start) - start).total_seconds())) if start else 0


# ------------------------------------------------------------------ step dispatch
def _run_step(db, run: m.Run, idx: int, steps: list[dict], work: dict) -> None:
    ctx = run.params.get("context", {})
    name = steps[idx]["name"]
    if idx == 0:
        _walk_items(db, run, steps, idx, work)
        if run.module in catalog.SCOPE and not in_scope(run.question, run.module, ctx):
            work["relevant"] = False   # answered locally; the billed service is never called
        return
    if name in SEARCH_STEPS:
        if run.params.get("ai_offline"):
            _walk_items(db, run, steps, idx, work)   # sample data: database content only, no AI text
        else:
            _search_with_service(db, run, steps, idx, work)
        return
    if "service" not in work and not run.params.get("ai_offline") and work.get("relevant", True):
        # resumed after the search stage without its answer saved (e.g. stopped mid-run): ask the service again
        search_idx = next((i for i, s in enumerate(steps[:idx]) if s["name"] in SEARCH_STEPS), None)
        if search_idx is not None:
            _search_with_service(db, run, steps, search_idx, work)
    if name == "Evaluate clinical evidence":
        work["findings"] = [{"text": r.get("snippet") or r.get("title", ""), "source": r.get("source"),
                             "evidence": catalog.evidence_kind(f"{r.get('title', '')} {r.get('snippet', '')}"),
                             "preliminary": True} for r in work.get("records", [])][:3]
    if name == "Draft report" and not run.params.get("ai_offline"):
        _write_report_sections(db, run, steps, idx, work)
    _walk_items(db, run, steps, idx, work)
    if name == "Draft answer":
        work["answer"] = build_ai_result(db, run, work)
    elif name == "Build views and answer":
        work["answer"] = build_pipeline_result(db, run, work)
    elif name == "Draft report":
        work["answer"] = build_cioa_result(db, run, work)


def _require_service() -> None:
    if not scientific_search.configured():
        raise RuntimeError("The AI Scientific Search service is not configured (AI_SEARCH_BASE_URL)")


def _search_with_service(db, run: m.Run, steps: list[dict], idx: int, work: dict) -> None:
    """One call to the AI Scientific Search service: retrieval and the cited answer. Afterwards the stage lists the
    sources the service actually used (e.g. PubMed - Searched)."""
    _require_service()
    for it in steps[idx]["items"]:
        it["status"] = "active"
    _log(work, "Asking the AI Scientific Search service")
    _persist(db, run, steps, work)
    try:
        body = scientific_search.search(run.question)
    except scientific_search.ScientificSearchError as e:
        for it in steps[idx]["items"]:
            it["status"] = "failed"
        raise RuntimeError(str(e)) from None
    parts = scientific_search.to_result_parts(body)
    names = list(dict.fromkeys(r["source"] for r in parts["records"])) or [steps[idx]["items"][0]["label"]]
    steps[idx]["items"] = [{"label": n, "status": "done"} for n in names]
    work["records"], work["errors"] = parts["records"], []
    work["sources_searched"] = names
    work["service"] = {k: parts[k] for k in ("answer", "references", "top_sources", "key_findings",
                                             "retrieved_chunk_count", "source_count")}
    _log(work, f"Found {parts['source_count']} sources ({parts['retrieved_chunk_count']} passages)")
    _persist(db, run, steps, work)


def section_question(run: m.Run, title: str) -> str:
    """The question sent to the service for one Competitive Insights report section."""
    p = run.params or {}
    ctx = p.get("context", {})
    mt = p.get("molecular_target") or ctx.get("target", "IRAK4")
    inds = ", ".join(p.get("indications") or ctx.get("indications") or [])
    scope = f" for {mt}" + (f" in {inds}" if inds else "")
    return f"{title}{scope}: {run.question}"


def _write_report_sections(db, run: m.Run, steps: list[dict], idx: int, work: dict) -> None:
    """One service call per report section, a few at a time. Finished sections are kept in the run, so Retry after
    a failure only asks for the missing ones (no second bill for sections already written)."""
    _require_service()
    w = catalog.resolve_workflow(run.workflow or run.params.get("workflow"))
    done = work.setdefault("sections_done", {})
    todo = [t for t in w["sections"] if t not in done]
    if not todo:
        return
    if steps[idx]["items"]:
        steps[idx]["items"][-1]["status"] = "active"
    _log(work, f"Writing {len(todo)} report sections")
    _persist(db, run, steps, work)
    errors = []
    with ThreadPoolExecutor(max_workers=SECTION_WORKERS) as pool:
        futures = {pool.submit(scientific_search.search, section_question(run, t)): t for t in todo}
        for fut in as_completed(futures):          # results are saved from this thread only
            title = futures[fut]
            try:
                parts = scientific_search.to_result_parts(fut.result())
            except scientific_search.ScientificSearchError as e:
                errors.append(f"{title}: {e}")
                _log(work, f"Could not write section: {title}")
            else:
                done[title] = {k: parts[k] for k in ("answer", "references", "key_findings")}
                _log(work, f"Wrote section: {title}")
            _persist(db, run, steps, work)
    if errors:
        raise RuntimeError(f"{len(errors)} of {len(w['sections'])} report sections could not be written ({errors[0]})")


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
                     step_label=f"Step {i + 1} of {len(steps)} - {steps[i]['name']}"[:300])
            try:
                _run_step(db, run, i, steps, work)
            except Exception as e:  # noqa: BLE001
                steps[i]["status"] = "failed"
                _set(db, run, steps=steps, status="Failed", error=f"{e}", step_label=f"Failed at step {i + 1} - {e}"[:300],
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
        db.add(m.Notification(user_id=run.user_id, title=title[:200], body=body[:500], route=run.module))
        db.commit()


# ------------------------------------------------------------------ result builders
def not_relevant_result(db, run: m.Run) -> dict:
    return {"relevant": False,
            "message": f"Please ask a relevant question. I can answer {catalog.SCOPE.get(run.module, 'questions in this module')}. "
                       "I could not match your question to that scope, so I have not generated an answer.",
            "follow_ups": [], "follow_ups_title": None}


def _service_parts(work: dict) -> dict:
    service = work.get("service")
    if not service:
        raise RuntimeError("No answer was received from the AI Scientific Search service")
    return service


def build_ai_result(db, run: m.Run, work: dict) -> dict:
    service = _service_parts(work)
    records = work.get("records", [])
    return {
        "relevant": True,
        "answer": service["answer"],       # Markdown; [n] citations match references[].number
        "answer_format": "markdown",
        "summary": service["answer"],      # kept for clients built against v0.1
        "key_findings": service["key_findings"],
        "top_sources": service["top_sources"],   # "View Details" drawer
        "references": service["references"],     # numbered reference list under the answer
        "follow_ups": [],                  # the service does not suggest follow-up questions
        "follow_ups_title": None,
        "evidence": records,
        "evidence_quality": {"verified": len(records), "assumptions": 0},
        "provider": "ai_scientific_search",
        "retrieved_passages": service.get("retrieved_chunk_count"),
    }


def query_assets(db, filters: dict | None, more: dict | None) -> list[dict]:
    rows = [p.as_row() for p in db.scalars(select(m.PipelineProgram).order_by(m.PipelineProgram.id)).unique()]
    return pl.filter_assets(rows, filters, more)


def pipeline_views(assets: list[dict], inds: list[str], sort: str = "stage", desc: bool = False) -> dict:
    return {"assets": pl.sort_assets(assets, sort, desc), "kpis": pl.kpis(assets), "kpi_tiles": pl.kpi_tiles(assets),
            "swimlane": pl.swimlane(assets, inds), "stage_counts": pl.stage_counts(assets, inds),
            "heatmap": pl.heatmap(assets, inds), "radar": pl.radar(assets, inds), "total": len(assets)}


def build_pipeline_result(db, run: m.Run, work: dict | None = None) -> dict:
    """Programs, KPIs and the five views come from the database; the written summary from the AI service."""
    work = work or {}
    f = run.params.get("filters") or {}
    more = run.params.get("more_filters") or {}
    assets = query_assets(db, f, more)
    inds = [f["indication"]] if f.get("indication") else run.params.get("context", {}).get("indications") or ["SLE", "RA", "AD"]
    service = None if run.params.get("ai_offline") else _service_parts(work)
    return {"relevant": True, "summary": service["answer"] if service else None, "answer_format": "markdown",
            "filters": f, "more_filters": more, "more_filters_count": catalog.more_filters_count(more),
            **pipeline_views(assets, inds),
            "top_sources": service["top_sources"] if service else [],
            "references": service["references"] if service else [],
            "follow_ups": [], "follow_ups_title": None,
            "provider": "ai_scientific_search" if service else None}


def build_cioa_result(db, run: m.Run, work: dict) -> dict:
    """Stats and asset tables come from the database; takeaways and section text from the AI service."""
    p = run.params
    w = catalog.resolve_workflow(run.workflow or p.get("workflow"))
    ctx = p.get("context", {})
    inds = p.get("indications") or ctx.get("indications") or []
    mt = p.get("molecular_target") or ctx.get("target", "IRAK4")
    offline = bool(p.get("ai_offline"))
    overview = None if offline else _service_parts(work)
    written = work.get("sections_done", {})
    assets = [a for a in query_assets(db, {}, {}) if not inds or a["indication"] in inds]
    stats = [
        {"value": len(assets), "label": "Programs tracked", "icon": "capsule"},
        {"value": sum(1 for a in assets if a.get("activity") in pl.LIVE), "label": "Active in the clinic", "icon": "pulse"},
        {"value": len({a["sponsor"] for a in assets}), "label": "Sponsors", "icon": "users"},
    ]
    asset_table = {"headers": ["Company", "Asset", "Mechanism", "Modality", "Phase", "Indication", "Status"],
                   "rows": [[a["sponsor"], a["name"], a["target"], a["modality"], a["stage"], a["indication"], a["activity"]]
                            for a in sorted(assets, key=lambda a: -pl.STAGE_ORDER.get(a["stage"], 0))]}

    def section(i: int, t: str) -> dict:
        s = written.get(t)
        # sections about programs / the landscape also carry the asset table (database)
        table = asset_table if any(k in t for k in ("Pipeline", "Landscape", "Peer Set")) and assets else None
        return {"index": i, "title": t, "bullets": s["key_findings"] if s else [], "text": s["answer"] if s else None,
                "references": s["references"] if s else [], "table": table}

    return {
        "relevant": True,
        "workflow": {"key": w["key"], "name": w["name"], "short": w["short"], "icon": w["icon"]},
        "context": {"therapeutic_area": p.get("therapeutic_area") or catalog.DEFAULT_THERAPEUTIC_AREA,
                    "indications": inds, "molecular_target": mt},
        "stats": stats,
        "key_takeaways": overview["key_findings"] if overview else [],
        "overview": overview["answer"] if overview else None,
        "overview_references": overview["references"] if overview else [],
        "sections": [section(i, t) for i, t in enumerate(w["sections"])],
        "data_sources": w["sources"],
        "provider": "ai_scientific_search" if overview else None,
    }
