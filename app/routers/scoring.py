"""Prioritization Scoring: configurable criteria/weights, AI-recommended scores, human overrides with
justification, scenarios and sensitivity. All math lives in app.services.scoring (pure + unit-tested)."""
import copy
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import DB, CurrentUser, audit, require
from app.db import models as m
from app.services import scoring as sc

router = APIRouter(prefix="/scoring", tags=["scoring"])


def ai_recommend_scores(target: str, indication: str, criteria: list[dict]) -> dict:
    """AI-recommended 1-5 score per criterion. The AI Scientific Search service has no scoring capability yet (it
    answers literature questions, and scoring questions currently fail on its side), so every score starts as None
    ('Missing') and users enter scores as overrides with a justification. Connect a scoring endpoint here when the
    AI team provides one; nothing is made up meanwhile."""
    return {c["key"]: None for c in criteria}


def load(db, aid: int, user: m.User, need_owner: bool = False) -> m.Assessment:
    a = db.get(m.Assessment, aid)
    if not a or (a.owner_id != user.id and "administer" not in user.permission_codes):
        raise HTTPException(404, "Assessment not found")
    return a


def keys_of(a: m.Assessment) -> list[str]:
    return [c["key"] for c in a.criteria]


def view(a: m.Assessment) -> dict:
    keys = keys_of(a)
    res = sc.calculate(a.indications, keys, a.weights, a.ai_scores, a.overrides)
    errs = sc.validate_weights(a.weights, keys)
    return {"id": a.id, "title": a.title, "target": a.target, "status": a.status, "indications": a.indications,
            "criteria": a.criteria, "weights": a.weights, "weight_total": sum(a.weights.get(k, 0) for k in keys),
            "weight_errors": errs, "scale_labels": sc.SCALE_LABELS,
            "ai_scores": a.ai_scores, "overrides": a.overrides,
            "results": [{"indication": r.indication, "scores": r.scores, "raw": r.raw, "weighted": r.weighted,
                         "norm": r.norm, "rank": r.rank, "priority": r.priority, "missing": r.missing} for r in res]}


# --------------------------------------------------------------------------- models + assessments
@router.get("/models")
def models(db: DB, user: CurrentUser):
    rows = db.scalars(select(m.ScoringModel).where(m.ScoringModel.status == "Published")).all()
    return [{"id": r.id, "name": r.name, "version": r.version, "criteria": len(r.criteria)} for r in rows]


class NewAssessment(BaseModel):
    model_id: int
    title: str | None = None


@router.post("/assessments", status_code=201)
def create_assessment(body: NewAssessment, db: DB, user: m.User = Depends(require("create"))):
    model = db.get(m.ScoringModel, body.model_id)
    if not model or model.status != "Published":
        raise HTTPException(404, "Scoring model not found or not published")
    ctx = user.context
    crit = copy.deepcopy(model.criteria)
    a = m.Assessment(owner_id=user.id, model_id=model.id, title=body.title or f"{ctx['target']} {model.name}",
                     target=ctx["target"], indications=ctx["indications"], criteria=crit,
                     weights={c["key"]: c.get("weight", 0) for c in crit},
                     ai_scores={i: ai_recommend_scores(ctx["target"], i, crit) for i in ctx["indications"]})
    db.add(a)
    db.commit()
    return view(a)


@router.get("/assessments")
def list_assessments(db: DB, user: CurrentUser):
    rows = db.scalars(select(m.Assessment).where(m.Assessment.owner_id == user.id).order_by(m.Assessment.updated_at.desc())).all()
    return [{"id": a.id, "title": a.title, "status": a.status, "updated_at": a.updated_at} for a in rows]


@router.get("/assessments/{aid}")
def get_assessment(aid: int, db: DB, user: CurrentUser):
    return view(load(db, aid, user))


class WeightsIn(BaseModel):
    weights: dict[str, float]


@router.put("/assessments/{aid}/weights")
def apply_weights(aid: int, body: WeightsIn, db: DB, user: m.User = Depends(require("edit"))):
    """'Apply & Recalculate'. Rejects anything that doesn't total 100%."""
    a = load(db, aid, user)
    unknown = set(body.weights) - set(keys_of(a))
    if unknown:
        raise HTTPException(422, f"Unknown criteria: {sorted(unknown)}")
    errs = sc.validate_weights(body.weights, keys_of(a))
    if errs:
        raise HTTPException(422, errs)
    a.weights = body.weights
    db.commit()
    return view(a)


class CriterionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rule: str = ""
    scale: dict[str, str] = {}


@router.post("/assessments/{aid}/criteria", status_code=201)
def add_criterion(aid: int, body: CriterionIn, db: DB, user: m.User = Depends(require("edit"))):
    a = load(db, aid, user)
    key = re.sub(r"\W+", "_", body.name.lower()).strip("_") + "_" + uuid.uuid4().hex[:4]
    a.criteria = [*a.criteria, {"key": key, "name": body.name, "rule": body.rule, "scale": body.scale, "weight": 0}]
    a.weights = {**a.weights, key: 0}  # weight 0 until the user rebalances to 100
    for i in a.indications:  # new criterion has no AI score yet -> shows as 'Missing'
        a.ai_scores = {**a.ai_scores, i: {**a.ai_scores.get(i, {}), key: None}}
    db.commit()
    return view(a)


@router.patch("/assessments/{aid}/criteria/{key}")
def edit_criterion(aid: int, key: str, body: CriterionIn, db: DB, user: m.User = Depends(require("edit"))):
    a = load(db, aid, user)
    if key not in keys_of(a):
        raise HTTPException(404, "Criterion not found")
    a.criteria = [{**c, "name": body.name, "rule": body.rule, "scale": body.scale or c.get("scale", {})} if c["key"] == key else c
                  for c in a.criteria]
    db.commit()
    return view(a)


@router.delete("/assessments/{aid}/criteria/{key}")
def remove_criterion(aid: int, key: str, db: DB, user: m.User = Depends(require("edit"))):
    a = load(db, aid, user)
    a.criteria = [c for c in a.criteria if c["key"] != key]
    a.weights = {k: v for k, v in a.weights.items() if k != key}
    a.ai_scores = {i: {k: v for k, v in s.items() if k != key} for i, s in a.ai_scores.items()}
    a.override_rows.clear()  # prototype behaviour: removing a criterion clears overrides
    db.commit()
    return view(a)  # weight_errors tells the UI to rebalance


@router.post("/assessments/{aid}/reset")
def reset(aid: int, db: DB, user: m.User = Depends(require("edit"))):
    a = load(db, aid, user)
    model = db.get(m.ScoringModel, a.model_id)
    a.criteria = copy.deepcopy(model.criteria)
    a.weights = {c["key"]: c.get("weight", 0) for c in a.criteria}
    a.ai_scores = {i: ai_recommend_scores(a.target, i, a.criteria) for i in a.indications}
    a.override_rows.clear()
    db.commit()
    return view(a)


class OverrideIn(BaseModel):
    indication: str
    criterion: str
    score: int | None = Field(default=None, ge=1, le=5)  # null = mark as Missing
    justification: str = ""


@router.put("/assessments/{aid}/overrides")
def set_override(aid: int, body: OverrideIn, db: DB, user: m.User = Depends(require("edit"))):
    a = load(db, aid, user)
    if body.indication not in a.indications or body.criterion not in keys_of(a):
        raise HTTPException(422, "Unknown indication or criterion")
    ai = a.ai_scores.get(body.indication, {}).get(body.criterion)
    row = next((o for o in a.override_rows if o.indication == body.indication and o.criterion_key == body.criterion), None)
    if body.score == ai:  # setting back to the AI value removes the override
        if row:
            a.override_rows.remove(row)
    else:
        if not body.justification.strip():
            raise HTTPException(422, "Justification is required to override an AI-recommended score")
        if row is None:
            row = m.AssessmentOverride(indication=body.indication, criterion_key=body.criterion)
            a.override_rows.append(row)
        row.score, row.justification, row.user_id = body.score, body.justification.strip(), user.id
        row.created_at = datetime.now(timezone.utc)
        audit(db, user, f"Overrode {body.indication}/{body.criterion}: AI {ai} -> {body.score}", "assessment", a.id)
    db.commit()
    return view(a)


@router.get("/assessments/{aid}/sensitivity")
def sensitivity(aid: int, db: DB, user: CurrentUser):
    a = load(db, aid, user)
    if sc.validate_weights(a.weights, keys_of(a)):
        raise HTTPException(409, "Weights must total 100% first")
    names = {c["key"]: c["name"] for c in a.criteria}
    out = sc.sensitivity(a.indications, keys_of(a), a.weights, a.ai_scores, a.overrides)
    for s in out["scenarios"]:
        s["label"] = f"{names[s['criterion']]} x2"
    return out


# --------------------------------------------------------------------------- scenarios
class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.post("/assessments/{aid}/scenarios", status_code=201)
def save_scenario(aid: int, body: ScenarioIn, db: DB, user: m.User = Depends(require("edit"))):
    a = load(db, aid, user)
    rk = sc.ranking(sc.calculate(a.indications, keys_of(a), a.weights, a.ai_scores, a.overrides))
    s = m.Scenario(name=body.name, weights=a.weights, overrides=a.overrides, ranking=sorted(rk, key=rk.get))
    a.scenarios.append(s)
    db.commit()
    return {"id": s.id, "name": s.name, "ranking": s.ranking}


@router.get("/assessments/{aid}/scenarios")
def list_scenarios(aid: int, db: DB, user: CurrentUser):
    a = load(db, aid, user)
    return [{"id": s.id, "name": s.name, "weights": s.weights, "ranking": s.ranking, "created_at": s.created_at}
            for s in a.scenarios]


@router.post("/assessments/{aid}/scenarios/{sid}/load")
def load_scenario(aid: int, sid: int, db: DB, user: m.User = Depends(require("edit"))):
    a = load(db, aid, user)
    s = next((x for x in a.scenarios if x.id == sid), None)
    if not s:
        raise HTTPException(404, "Scenario not found")
    a.weights = copy.deepcopy(s.weights)
    a.override_rows.clear()
    db.flush()  # remove old rows before re-adding the scenario's cells (unique per assessment/indication/criterion)
    for ind, cells in (s.overrides or {}).items():
        for key, o in cells.items():
            at = o.get("at")
            a.override_rows.append(m.AssessmentOverride(
                indication=ind, criterion_key=key, score=o.get("score"), justification=o.get("justification") or "",
                user_id=o.get("user_id") or user.id,
                created_at=datetime.fromisoformat(at) if at else datetime.now(timezone.utc)))
    db.commit()
    return view(a)


@router.delete("/assessments/{aid}/scenarios/{sid}", status_code=204)
def delete_scenario(aid: int, sid: int, db: DB, user: m.User = Depends(require("edit"))):
    a = load(db, aid, user)
    a.scenarios = [s for s in a.scenarios if s.id != sid]
    db.commit()


@router.post("/assessments/{aid}/save-to-workspace")
def to_workspace(aid: int, db: DB, user: CurrentUser):
    a = load(db, aid, user)
    snap = view(a)
    top = next((r for r in sorted(snap["results"], key=lambda r: r["rank"] or 99) if r["rank"]), None)
    item = m.WorkspaceItem(owner_id=user.id, title=a.title, item_type="Assessment", module="scoring", folder="",
                           question=f"Prioritise {', '.join(a.indications)} for {a.target}", snapshot=snap,
                           summary=f"{top['indication']} ranks first ({top['norm']})." if top else "Draft assessment",
                           status="Saved" if not snap["weight_errors"] else "Draft")
    item.versions.append(m.ItemVersion(label="v1", note="Saved assessment", snapshot=snap))
    db.add(item)
    db.commit()
    return {"item_id": item.id}
