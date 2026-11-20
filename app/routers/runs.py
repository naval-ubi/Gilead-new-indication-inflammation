"""One generic job API for all three research modules + module-specific read endpoints.

Frontend flow (identical for AI Search / Pipeline / CIOA):
  POST /runs                 -> 202 {id}            (user presses Ask / Build / Run)
  GET  /runs/{id}            -> status + steps      (poll every ~1s)  or  GET /runs/{id}/events (SSE)
  GET  /runs/{id}/result     -> result payload once Completed
  POST /runs/{id}/cancel | /pause | /resume | /rerun
"""
import asyncio
import json
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import DB, CurrentUser, audit, require
from app.db import models as m
from app.db.base import SessionLocal
from app.services import pipeline as pl
from app.services.integrations import improve_prompt, words
from app.services.orchestrator import CAPABILITIES, execute_run, plan_steps

router = APIRouter(tags=["research"])

Module = Literal["ai", "pipeline", "cioa"]
KIND = {"ai": "Search", "pipeline": "Workflow run", "cioa": "Workflow run"}


class RunIn(BaseModel):
    module: Module
    question: str = Field(default="", max_length=4000)
    deep_research: bool = False
    sources: list[str] | None = None              # AI Search: subset of enabled connectors
    filters: dict | None = None                    # Pipeline: {target, indication, modality, stage}
    capabilities: list[int] | None = None          # CIOA: indices into /cioa/capabilities
    therapeutic_area: str | None = None
    modality_target: str | None = None
    parent_run_id: int | None = None               # follow-up question


def auto_question(f: dict, ctx: dict) -> str:  # mirrors pAuto() when only filters are chosen
    return (f"Show the drug development pipeline for {f.get('target') or ctx.get('target', 'IRAK4') + '-related programs'} "
            f"in {f.get('indication') or ', '.join(ctx.get('indications', []))}"
            f"{' (' + f['modality'] + ')' if f.get('modality') else ''}{' at ' + f['stage'] if f.get('stage') else ''}.")


def run_out(r: m.Run, full: bool = False) -> dict:
    d = {"id": r.id, "module": r.module, "kind": r.kind, "title": r.title, "question": r.question, "mode": r.mode,
         "status": r.status, "step_index": r.step_index, "step_label": r.step_label, "steps": r.steps,
         "progress_pct": 100 if r.status == "Completed" else int(100 * r.step_index / max(1, len(r.steps))),
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


@router.post("/runs", status_code=202)
def create_run(body: RunIn, bg: BackgroundTasks, db: DB, user: m.User = Depends(require("execute"))):
    ctx = user.context
    q = body.question.strip()
    if body.module == "pipeline":
        q = q or auto_question(body.filters or {}, ctx)
        if not q and not any((body.filters or {}).values()):
            raise HTTPException(422, "Type a question or choose a filter first")
    elif not q:
        raise HTTPException(422, "Type a research question first")
    run = m.Run(user_id=user.id, module=body.module, kind=KIND[body.module], question=q,
                title=q if len(q) <= 70 else q[:67] + "...", project=ctx.get("session", ""),
                mode="Deep Research" if body.deep_research else "Standard", parent_run_id=body.parent_run_id,
                params={"context": ctx, "sources": body.sources, "filters": body.filters,
                        "capabilities": body.capabilities, "therapeutic_area": body.therapeutic_area,
                        "modality_target": body.modality_target})
    run.steps = plan_steps(run)
    run.step_label = f"Step 1 of {len(run.steps)}"
    db.add(run)
    db.commit()
    bg.add_task(execute_run, run.id)
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
            key = (snap["status"], snap["step_index"])
            if key != last:
                last = key
                yield f"data: {json.dumps(snap, default=str)}\n\n"
            if snap["status"] in ("Completed", "Failed", "Cancelled", "Paused"):
                return
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
    if r.status not in ("Paused", "Failed"):
        raise HTTPException(409, f"Cannot resume a run that is {r.status}")
    r.status = "In Progress"
    db.commit()
    bg.add_task(execute_run, r.id)  # re-enters at persisted step_index with persisted partial output
    return run_out(r)


@router.post("/runs/{run_id}/rerun", status_code=202)
def rerun(run_id: int, bg: BackgroundTasks, db: DB, user: m.User = Depends(require("execute"))):
    old = own_run(db, run_id, user)
    new = m.Run(user_id=user.id, module=old.module, kind=old.kind, title=old.title, question=old.question,
                mode=old.mode, params=old.params, project=old.project, parent_run_id=old.id)
    new.steps = plan_steps(new)
    db.add(new)
    db.commit()
    bg.add_task(execute_run, new.id)
    return run_out(new)


class SaveIn(BaseModel):
    folder: str = ""
    title: str | None = None


@router.post("/runs/{run_id}/save")
def save_run(run_id: int, body: SaveIn, db: DB, user: CurrentUser):
    """'Save Search' button: freezes question, context, filters AND result into the Workspace."""
    r = own_run(db, run_id, user)
    existing = db.scalar(select(m.WorkspaceItem).where(m.WorkspaceItem.run_id == r.id))
    if existing:
        return {"item_id": existing.id, "already_saved": True}
    type_map = {"ai": "Search", "pipeline": "Pipeline view", "cioa": "Report"}
    snap = {k: v for k, v in (r.result or {}).items() if not k.startswith("_")} or None
    item = m.WorkspaceItem(owner_id=user.id, title=body.title or r.title, item_type=type_map[r.module], module=r.module,
                           folder=body.folder or r.project, question=r.question, params=r.params, snapshot=snap,
                           source_count=r.source_count, run_id=r.id, status="Completed" if r.module == "cioa" else "Saved",
                           summary=(snap or {}).get("summary", ""))
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


# ----------------------------------------------------------------- prompt assist + CIOA/pipeline metadata
class SuggestIn(BaseModel):
    text: str


@router.post("/prompts/suggest")
def suggest(body: SuggestIn, user: CurrentUser):
    """'AI Suggestion ... Use It' - shown by the UI once the user has typed >= 2 words."""
    if words(body.text) < 2:
        return {"suggestion": None}
    return {"suggestion": improve_prompt(body.text)}


@router.get("/prompts/suggested")
def suggested_prompts(db: DB, user: CurrentUser, module: str | None = None):
    rows = db.scalars(select(m.AdminRecord).where(m.AdminRecord.kind == "prompt", m.AdminRecord.status == "Published")).all()
    return [r.name for r in rows]


@router.get("/cioa/capabilities")
def capabilities(user: CurrentUser):
    return [{"index": i, "name": n, "description": d} for i, (n, d) in enumerate(CAPABILITIES)]


@router.get("/pipeline/options")
def pipeline_options(db: DB, user: CurrentUser):
    def distinct(col):
        return sorted(x for x in db.scalars(select(col).distinct()) if x)
    return {"targets": distinct(m.PipelineAsset.target), "indications": distinct(m.PipelineAsset.indication),
            "modalities": distinct(m.PipelineAsset.modality), "stages": pl.STAGES}


@router.get("/pipeline/assets")
def pipeline_assets(db: DB, user: CurrentUser, target: str | None = None, indication: str | None = None,
                    modality: str | None = None, stage: str | None = None, sort: str = "stage",
                    desc: bool = False, view: Literal["table", "swimlane", "graph", "heatmap", "radar"] = "table"):
    """Live filter endpoint: the Pipeline results page re-queries this when a filter dropdown changes
    (no need to start a new run). Initial results come from the run; filtering is cheap and synchronous."""
    q = select(m.PipelineAsset)
    for col, val in [("target", target), ("indication", indication), ("modality", modality), ("stage", stage)]:
        if val:
            q = q.where(getattr(m.PipelineAsset, col) == val)
    assets = [{c.name: getattr(a, c.name) for c in a.__table__.columns if c.name != "updated_at"} for a in db.scalars(q)]
    inds = [indication] if indication else user.context.get("indications") or ["SLE", "RA", "AD"]
    out = {"kpis": pl.kpis(assets), "total": len(assets)}
    out["data"] = {"table": lambda: pl.sort_assets(assets, sort, desc), "swimlane": lambda: pl.swimlane(assets, inds),
                   "graph": lambda: pl.stage_counts(assets, inds), "heatmap": lambda: pl.heatmap(assets, inds),
                   "radar": lambda: pl.radar(assets, inds)}[view]()
    return out
