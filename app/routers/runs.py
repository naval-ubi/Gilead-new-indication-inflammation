"""One generic job API for all three research modules + module-specific read endpoints.

Frontend flow (identical for AI Search / Pipeline / CIOA):
  POST /runs                 -> 202 {id}            (user presses Ask / Build / Run)
  GET  /runs/{id}            -> status + steps      (poll every ~1s)  or  GET /runs/{id}/events (SSE)
  GET  /runs/{id}/result     -> result payload once Completed
  POST /runs/{id}/cancel | /pause | /resume | /rerun | /notify
  GET  /runs/{id}/thread     -> every turn of the conversation the run belongs to (follow-ups)

Competitive Insights (module "cioa"): pick a workflow from GET /cioa/workflows, then POST /runs with `workflow`.
"""
import asyncio
import json
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import DB, CurrentUser, audit, require
from app.db import models as m
from app.db.base import SessionLocal
from app.services import catalog, scientific_search
from app.services import pipeline as pl
from app.services.integrations import words
from app.services.orchestrator import (CAPABILITIES, elapsed_seconds, execute_run, is_stalled, pipeline_views,
                                       plan_steps, query_assets)

router = APIRouter(tags=["research"])

Module = Literal["ai", "pipeline", "cioa"]
KIND = {"ai": "Search", "pipeline": "Workflow run", "cioa": "Workflow run"}


class RunIn(BaseModel):
    module: Module
    question: str = Field(default="", max_length=4000)
    deep_research: bool = False
    sources: list[str] | None = None              # AI Search: subset of enabled connectors
    filters: dict | None = None                    # Pipeline: {target, indication, modality, stage}
    more_filters: dict | None = None               # Pipeline: "More filters" dialog {status, sex, age[], phase[], ...}
    capabilities: list[int] | None = None          # legacy CIOA: indices into /cioa/capabilities
    workflow: str | None = None                    # Competitive Insights: workflow key (or index) from /cioa/workflows
    therapeutic_area: str | None = None            # Competitive Insights dropdowns
    indication: str | None = None
    molecular_target: str | None = None
    modality_target: str | None = None             # deprecated alias of molecular_target
    parent_run_id: int | None = None               # follow-up question: continues that run's thread
    notify: bool = False                           # "Notify me when ready"


def auto_question(f: dict, ctx: dict) -> str:  # mirrors pAuto() when only filters are chosen
    return (f"Show the drug development pipeline for {f.get('target') or ctx.get('target', 'IRAK4') + '-related programs'} "
            f"in {f.get('indication') or ', '.join(ctx.get('indications', []))}"
            f"{' (' + f['modality'] + ')' if f.get('modality') else ''}{' at ' + f['stage'] if f.get('stage') else ''}.")


def progress_pct(r: m.Run) -> int:
    """Share of sub-items finished across all steps (smoother than whole steps for the progress bar)."""
    if r.status == "Completed":
        return 100
    items = [i for s in r.steps or [] for i in (s.get("items") or [{"status": s.get("status")}])]
    done = sum(1 for i in items if i.get("status") in ("done", "failed", "skipped"))
    return min(99, int(100 * done / max(1, len(items))))


def run_out(r: m.Run, full: bool = False) -> dict:
    work = (r.result or {}).get("_work", {}) if r.status != "Completed" else {}
    wf = catalog.resolve_workflow(r.workflow) if r.module == "cioa" and r.workflow in catalog.WORKFLOW_BY_KEY else None
    d = {"id": r.id, "module": r.module, "module_name": catalog.MODULE_NAMES.get(r.module, r.module), "kind": r.kind,
         "title": r.title, "question": r.question, "mode": r.mode, "status": r.status, "step_index": r.step_index,
         "step_label": r.step_label, "steps": r.steps, "progress_pct": progress_pct(r),
         "current_activity": next((i["label"] for s in r.steps or [] if s.get("status") == "active"
                                   for i in s.get("items", []) if i.get("status") == "active"), None),
         "activity": work.get("activity", [])[-6:],                 # "Live activity" panel
         "emerging_findings": work.get("findings", []),             # "Emerging findings" (preliminary)
         "elapsed_seconds": elapsed_seconds(r), "thread_id": r.thread_id, "parent_run_id": r.parent_run_id,
         "stalled": is_stalled(r),                                  # In Progress but no worker: offer Resume
         "resumable": r.status in ("Paused", "Failed") or is_stalled(r),
         "notify": r.notify, "workflow": {"key": wf["key"], "name": wf["name"], "short": wf["short"]} if wf else None,
         "error": r.error, "saved": r.saved, "approved": r.approved, "source_count": r.source_count,
         "params": r.params, "created_at": r.created_at, "updated_at": r.updated_at}
    if full:
        d["result"] = {k: v for k, v in (r.result or {}).items() if not k.startswith("_") and k != "trace"}
    return d


def own_run(db, run_id: int, user: m.User) -> m.Run:
    r = db.get(m.Run, run_id)
    if not r or r.user_id != user.id or r.deleted:
        raise HTTPException(404, "Run not found")
    return r


def start_run(db, bg: BackgroundTasks, user: m.User, *, module: str, question: str, mode: str, params: dict,
              workflow: str = "", parent: m.Run | None = None, notify: bool = False, title: str | None = None) -> m.Run:
    """Single place that creates + schedules a run (used by POST /runs, rerun, change-workflow, workspace rerun)."""
    params = {k: v for k, v in params.items() if k not in ("resolved_sources", "ai_service")}  # v0.4 fields
    params["ai_offline"] = scientific_search.is_offline()   # sample data only: no AI text, no service calls
    run = m.Run(user_id=user.id, module=module, kind=KIND[module], question=question, mode=mode,
                title=title or (question if len(question) <= 70 else question[:67] + "..."),
                project=params.get("context", {}).get("session", ""), params=params, workflow=workflow,
                parent_run_id=parent.id if parent else None, thread_id=(parent.thread_id or parent.id) if parent else None,
                notify=notify)
    run.steps = plan_steps(run)
    run.step_label = f"Step 1 of {len(run.steps)}"
    db.add(run)
    db.flush()
    run.thread_id = run.thread_id or run.id  # first turn starts a new conversation
    db.commit()
    bg.add_task(execute_run, run.id)
    return run


@router.post("/runs", status_code=202)
def create_run(body: RunIn, bg: BackgroundTasks, db: DB, user: m.User = Depends(require("execute"))):
    ctx = user.context
    q = body.question.strip()
    if body.module == "pipeline":
        if not q and not any((body.filters or {}).values()):
            raise HTTPException(422, "Type a question or choose a filter first")
        q = q or auto_question(body.filters or {}, ctx)
    elif not q:
        raise HTTPException(422, "Type a research question first")
    parent = own_run(db, body.parent_run_id, user) if body.parent_run_id else None
    if parent and parent.module != body.module:
        raise HTTPException(422, "A follow-up must stay in the same module as the conversation")
    workflow = ""
    if body.module == "cioa":
        try:
            workflow = (catalog.resolve_workflow(body.workflow) if body.workflow not in (None, "")
                        else catalog.recommend_workflow(q))["key"]
        except KeyError as e:
            raise HTTPException(422, str(e))
    inds = [body.indication] if body.indication else ctx.get("indications")
    run = start_run(db, bg, user, module=body.module, question=q, notify=body.notify, parent=parent, workflow=workflow,
                    mode="Deep Research" if body.deep_research else "Standard",
                    params={"context": ctx, "sources": body.sources, "filters": body.filters,
                            "more_filters": body.more_filters, "capabilities": body.capabilities,
                            "workflow": workflow or None, "therapeutic_area": body.therapeutic_area,
                            "indications": inds if body.module == "cioa" else None,
                            "molecular_target": body.molecular_target or body.modality_target})
    return run_out(run)


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: DB, user: CurrentUser):
    return run_out(own_run(db, run_id, user))


@router.get("/runs/{run_id}/result")
def get_result(run_id: int, db: DB, user: CurrentUser):
    r = own_run(db, run_id, user)
    if r.status != "Completed":
        raise HTTPException(409, f"Run is {r.status}")
    return run_out(r, full=True)


@router.get("/runs/{run_id}/thread")
def get_thread(run_id: int, db: DB, user: CurrentUser):
    """Conversation view: every turn (question + status + result when completed), oldest first.
    For Competitive Insights a turn is one report; 'Change workflow' adds a new turn."""
    r = own_run(db, run_id, user)
    tid = r.thread_id or r.id
    turns = db.scalars(select(m.Run).where(m.Run.thread_id == tid, m.Run.user_id == user.id, m.Run.deleted.is_(False))
                       .order_by(m.Run.id)).all() or [r]
    return {"thread_id": tid, "module": r.module, "module_name": catalog.MODULE_NAMES.get(r.module),
            "turns": [run_out(t, full=t.status == "Completed") for t in turns]}


class NotifyIn(BaseModel):
    enabled: bool = True


@router.post("/runs/{run_id}/notify")
def set_notify(run_id: int, body: NotifyIn, db: DB, user: CurrentUser):
    """'Notify me when ready' toggle. When on, a notification is sent on completion even if the user has
    turned in-app notifications off in Preferences."""
    r = own_run(db, run_id, user)
    r.notify = body.enabled
    db.commit()
    return {"id": r.id, "notify": r.notify, "status": r.status}


@router.get("/runs/{run_id}/events")
async def stream(run_id: int, user: CurrentUser):
    """Server-Sent Events: pushes a snapshot whenever step/status changes, closes when terminal."""
    def snapshot():
        with SessionLocal() as s:
            r = s.get(m.Run, run_id)
            return None if not r or r.user_id != user.id else run_out(r)

    async def gen():
        last = None
        while True:
            snap = await asyncio.to_thread(snapshot)
            if snap is None:
                yield "event: error\ndata: {\"detail\": \"not found\"}\n\n"
                return
            key = (snap["status"], snap["step_index"], snap["progress_pct"], len(snap["activity"]))
            if key != last:
                last = key
                yield f"data: {json.dumps(snap, default=str)}\n\n"
            if snap["status"] in ("Completed", "Failed", "Cancelled", "Paused") or snap["stalled"]:
                return  # stalled: nothing more will happen until the user resumes
            await asyncio.sleep(0.7)
    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: int, db: DB, user: CurrentUser):
    r = own_run(db, run_id, user)
    if r.status in ("In Progress", "Paused"):
        r.status = "Cancelled"
        db.commit()
    return run_out(r)


@router.post("/runs/{run_id}/pause")
def pause(run_id: int, db: DB, user: CurrentUser):
    r = own_run(db, run_id, user)
    if r.status == "In Progress":
        r.status = "Paused"
        db.commit()
    return run_out(r)


@router.post("/runs/{run_id}/resume", status_code=202)
def resume(run_id: int, bg: BackgroundTasks, db: DB, user: m.User = Depends(require("execute"))):
    r = own_run(db, run_id, user)
    if not (r.status in ("Paused", "Failed") or is_stalled(r)):
        raise HTTPException(409, f"Cannot resume a run that is {r.status}")
    r.status = "In Progress"
    r.params = {**(r.params or {}), "ai_offline": scientific_search.is_offline()}  # a sample run resumed by a user uses the AI service
    db.commit()
    bg.add_task(execute_run, r.id)  # re-enters at persisted step_index with persisted partial output
    return run_out(r)


@router.post("/runs/{run_id}/rerun", status_code=202)
def rerun(run_id: int, bg: BackgroundTasks, db: DB, user: m.User = Depends(require("execute"))):
    old = own_run(db, run_id, user)
    new = start_run(db, bg, user, module=old.module, question=old.question, mode=old.mode, params=old.params,
                    workflow=old.workflow, parent=old, notify=old.notify, title=old.title)
    return run_out(new)


class WorkflowIn(BaseModel):
    workflow: str


@router.post("/runs/{run_id}/change-workflow", status_code=202)
def change_workflow(run_id: int, body: WorkflowIn, bg: BackgroundTasks, db: DB, user: m.User = Depends(require("execute"))):
    """Competitive Insights 'Change workflow': same question, different workflow. The previous report stays in the
    conversation; any unfinished run in this thread is cancelled."""
    old = own_run(db, run_id, user)
    if old.module != "cioa":
        raise HTTPException(409, "Only Competitive Insights runs have a workflow")
    try:
        wf = catalog.resolve_workflow(body.workflow)
    except KeyError as e:
        raise HTTPException(422, str(e))
    if wf["key"] == old.workflow and old.status == "Completed":
        raise HTTPException(409, "This report already uses that workflow")
    if old.status in ("In Progress", "Paused"):
        old.status = "Cancelled"
    new = start_run(db, bg, user, module="cioa", question=old.question, mode=old.mode, workflow=wf["key"],
                    params={**old.params, "workflow": wf["key"]}, parent=old, notify=old.notify)
    return run_out(new)


class SaveIn(BaseModel):
    folder: str = Field(default="", max_length=120)
    title: str | None = Field(default=None, max_length=300)


@router.post("/runs/{run_id}/save")
def save_run(run_id: int, body: SaveIn, db: DB, user: CurrentUser):
    """'Save Search' button: freezes question, context, filters AND result into the Workspace."""
    r = own_run(db, run_id, user)
    existing = db.scalar(select(m.WorkspaceItem).where(m.WorkspaceItem.run_id == r.id))
    if existing:
        return {"item_id": existing.id, "already_saved": True}
    type_map = {"ai": "Search", "pipeline": "Pipeline view", "cioa": "Report"}
    snap = {k: v for k, v in (r.result or {}).items() if not k.startswith("_")} or None
    if r.status != "Completed":
        raise HTTPException(409, f"Run is {r.status}; save it once it has completed")
    item = m.WorkspaceItem(owner_id=user.id, title=body.title or r.title, item_type=type_map[r.module], module=r.module,
                           folder=body.folder or r.project, question=r.question, snapshot=snap,
                           params={**(r.params or {}), "mode": r.mode, "workflow": r.workflow or None},
                           source_count=r.source_count, run_id=r.id, status="Completed" if r.module == "cioa" else "Saved",
                           summary=(snap or {}).get("summary") or ((snap or {}).get("key_takeaways") or [""])[0])
    item.versions.append(m.ItemVersion(label="v1", note="Saved from run", snapshot=snap))
    r.saved = True
    db.add(item)
    db.commit()
    return {"item_id": item.id, "already_saved": False}


@router.post("/runs/{run_id}/approve")
def approve(run_id: int, db: DB, user: m.User = Depends(require("edit"))):
    """CIOA: owner approves the generated report."""
    r = own_run(db, run_id, user)
    if r.module != "cioa" or r.status != "Completed":
        raise HTTPException(409, "Only completed CIOA reports can be approved")
    r.approved = True
    audit(db, user, f"Approved report run {r.id}", "run", r.id)
    db.commit()
    return run_out(r)


@router.get("/runs/{run_id}/report")
def full_report(run_id: int, db: DB, user: CurrentUser):
    """'Export Full Report (PDF)': the complete, render-ready report document (cover metadata, at-a-glance
    figures, key takeaways, contents, every section, appendices). The PDF is rendered from this payload."""
    r = own_run(db, run_id, user)
    if r.module != "cioa" or r.status != "Completed":
        raise HTTPException(409, "Only completed Competitive Insights reports can be exported")
    res = r.result or {}
    wf = catalog.resolve_workflow(r.workflow)
    rs = res.get("research_summary", {})
    ctx = res.get("context", {})
    mf = catalog.more_filters_count((r.params or {}).get("more_filters"))
    generated = r.updated_at
    return {
        "report_id": f"CI-{r.id:06d}",
        "title": f"{wf['name']} report",
        "question": r.question,
        "generated_at": generated,
        "meta": [["Molecular target", ctx.get("molecular_target", "")], ["Indications", ", ".join(ctx.get("indications") or [])],
                 ["Therapeutic area", ctx.get("therapeutic_area", "")], ["Workflow", wf["name"]],
                 ["More filters", f"{mf} applied" if mf else "None"],
                 ["Sources searched", ", ".join(rs.get("sources_searched") or res.get("sources_used") or [])],
                 ["Generated", "AI Assistant"], ["Report ID", f"CI-{r.id:06d}"]],
        "notice": None if res.get("provider") else "This report has no AI-written text (sample data).",
        "at_a_glance": res.get("stats", []),
        "key_takeaways": res.get("key_takeaways", []),
        "contents": [s["title"] for s in res.get("sections", [])],
        "sections": res.get("sections", []),
        "appendices": [
            {"title": "Appendix A · Workflow subcategories", "items": wf["subcategories"]},
            {"title": "Appendix B · Method and limitations",
             "items": [f"Sources: {', '.join(rs.get('sources_searched') or [])}.",
                       "Content is assembled by the AI Assistant from enabled data connectors and the selected workflow template.",
                       "Program statuses change frequently; re-run the workflow before decisions."]},
        ],
        "footer": "Gilead Confidential · For internal use only",
        "filename": f"Competitive-Insights_{wf['short'].replace(' ', '-')}_{generated:%Y-%m-%d}.pdf",
    }


# ----------------------------------------------------------------- prompt assist + Competitive Insights/pipeline metadata
class SuggestIn(BaseModel):
    text: str


def _service_rephrase(text: str) -> str:
    if not scientific_search.configured():
        raise HTTPException(503, "AI Rephrase is unavailable: the AI Scientific Search service is not configured")
    try:
        return scientific_search.rephrase(text)
    except scientific_search.ScientificSearchError as e:
        raise HTTPException(503, f"AI Rephrase is unavailable right now: {e}")


@router.post("/prompts/suggest")
def suggest(body: SuggestIn, user: CurrentUser):
    """Legacy 'AI Suggestion' (previous UI): same as AI Rephrase. Returns null for fewer than 2 words."""
    if words(body.text) < 2:
        return {"suggestion": None}
    return {"suggestion": _service_rephrase(body.text)}


class RephraseIn(BaseModel):
    text: str = Field(max_length=4000)
    module: Module = "ai"


@router.post("/prompts/rephrase")
def rephrase(body: RephraseIn, user: CurrentUser):
    """'AI Rephrase' button on every ask box: the AI Scientific Search service rewrites the question; the user
    reviews it, then submits. 503 when the service is not configured or not answering."""
    if not body.text.strip():
        raise HTTPException(422, "Type a question first, then use AI Rephrase")
    return {"original": body.text, "rephrased": _service_rephrase(body.text)}


@router.get("/prompts/suggested")
def suggested_prompts(db: DB, user: CurrentUser, module: str | None = None):
    rows = db.scalars(select(m.AdminRecord).where(m.AdminRecord.kind == "prompt", m.AdminRecord.status == "Published")).all()
    return [r.name for r in rows]


@router.get("/cioa/capabilities")
def capabilities(user: CurrentUser):
    """Deprecated: subcategories of the default workflow. Use /cioa/workflows."""
    return [{"index": i, "name": n, "description": d} for i, (n, d) in enumerate(CAPABILITIES)]


@router.get("/cioa/options")
def cioa_options(user: CurrentUser):
    """Competitive Insights ask-box dropdowns: Therapeutic Area, Indication (narrowed by area), Molecular Target."""
    return {"therapeutic_areas": list(catalog.THERAPEUTIC_AREAS), "indications_by_area": catalog.THERAPEUTIC_AREAS,
            "all_indications": catalog.CORE_INDICATIONS + catalog.ADDITIONAL_INDICATIONS,
            "molecular_targets": catalog.MOLECULAR_TARGET_GROUPS, "default_therapeutic_area": catalog.DEFAULT_THERAPEUTIC_AREA,
            "example_questions": catalog.CIOA_EXAMPLE_QUESTIONS}


@router.get("/cioa/workflows")
def cioa_workflows(user: CurrentUser, question: str | None = None):
    """Workflow picker. Pass the user's question to get `recommended` (the workflow that best fits it)."""
    return {"workflows": [catalog.workflow_out(w) for w in catalog.WORKFLOWS],
            "recommended": catalog.recommend_workflow(question)["key"] if question else None}


@router.get("/pipeline/options")
def pipeline_options(db: DB, user: CurrentUser):
    def used(col, *joins):  # values that at least one pipeline program uses (as before normalisation)
        q = select(col).select_from(m.PipelineProgram)
        for j in joins:
            q = q.join(j)
        return sorted(x for x in db.scalars(q.distinct()) if x)
    return {"targets": used(m.Target.name, m.DrugAsset, m.Target), "indications": used(m.Indication.code, m.Indication),
            "modalities": used(m.Modality.name, m.DrugAsset, m.Modality), "stages": pl.STAGES,
            "sponsors": used(m.Sponsor.name, m.DrugAsset, m.Sponsor),
            # grouped dropdowns of the new UI (options may exist before any asset uses them)
            "groups": catalog.PIPELINE_FILTER_GROUPS, "hints": catalog.OPTION_HINTS,
            "stage_equivalents": pl.STAGE_ALIASES, "more_filters": catalog.MORE_FILTERS,
            "indication_names": catalog.INDICATION_FULL_NAMES}


View = Literal["table", "swimlane", "graph", "heatmap", "radar"]


def _assets_view(db, user: m.User, filters: dict, more: dict | None, view: View, sort: str, desc: bool) -> dict:
    assets = query_assets(db, filters, more)
    inds = [filters["indication"]] if filters.get("indication") else user.context.get("indications") or ["SLE", "RA", "AD"]
    v = pipeline_views(assets, inds, sort, desc)
    data = {"table": v["assets"], "swimlane": v["swimlane"], "graph": v["stage_counts"], "heatmap": v["heatmap"],
            "radar": v["radar"]}[view]
    return {"kpis": v["kpis"], "kpi_tiles": v["kpi_tiles"], "total": v["total"], "filters": filters,
            "more_filters_count": catalog.more_filters_count(more), "view": view, "data": data}


@router.get("/pipeline/assets")
def pipeline_assets(db: DB, user: CurrentUser, target: str | None = None, indication: str | None = None,
                    modality: str | None = None, stage: str | None = None, sort: str = "stage",
                    desc: bool = False, view: View = "table",
                    status: Literal["all", "rec"] | None = None, phase: list[str] | None = Query(None)):
    """Live filter endpoint: each pipeline answer in the conversation has its own filter bar and view tabs and
    re-queries this when they change (no new run). `stage` also accepts Approved, Regulatory review, On hold
    and Discontinued. `status`/`phase` are the program-level part of 'More filters'."""
    f = {"target": target, "indication": indication, "modality": modality, "stage": stage}
    return _assets_view(db, user, {k: v for k, v in f.items() if v}, {"status": status, "phase": phase}, view, sort, desc)


class AssetQuery(BaseModel):
    filters: dict = {}
    more_filters: dict | None = None
    view: View = "table"
    sort: str = "stage"
    desc: bool = False


@router.post("/pipeline/assets/query")
def pipeline_assets_query(body: AssetQuery, db: DB, user: CurrentUser):
    """Same as GET /pipeline/assets, with the full 'More filters' object in the body."""
    return _assets_view(db, user, {k: v for k, v in body.filters.items() if v}, body.more_filters, body.view,
                        body.sort, body.desc)
