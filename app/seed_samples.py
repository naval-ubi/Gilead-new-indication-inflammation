"""Sample data for frontend development and testing: fills every screen with realistic records.

Everything is created through the same functions the API endpoints use, so each record has exactly the shape the
frontend receives in real use. Timestamps are spread over the last ~6 weeks so History date filters have data.

What you get (log in as swanik@example.com / ChangeMe123!):
  Home / History  completed searches, pipeline answers and reports; a failed and two paused runs (Resume / Retry);
                  an out-of-scope question; runs older than 7 and 30 days
  Conversations   AI Search, Pipeline and Competitive Insights threads with follow-ups and a changed workflow
  Workspace       own items in folders, an archived item, a version history, notes, an item shared with a
                  colleague, items shared with you directly and through your group
  Scoring         an assessment with two justified overrides and two saved scenarios
  Notifications   read and unread

Run:  python -m app.seed_samples        (safe to repeat; to start over, delete gilead.db and start the API)
Or set SEED_SAMPLE_DATA=true in .env to load it on start-up.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks
from sqlalchemy import select

from app.db import models as m
from app.routers import runs as R
from app.routers import scoring as SC
from app.routers import workspace as W
from app.services.catalog import STARTER_QUESTIONS

log = logging.getLogger(__name__)
AI, PIPE, CI = STARTER_QUESTIONS["ai"], STARTER_QUESTIONS["pipeline"], STARTER_QUESTIONS["cioa"]
MARKER = AI[0]  # the first sample question; if Swanik already asked it, the samples are loaded
NOW = datetime.now(timezone.utc)


def _user(db, email: str) -> m.User:
    u = db.scalar(select(m.User).where(m.User.email == email))
    if u is None:
        raise RuntimeError(f"Demo user {email} not found. Sample data needs the demo users (SEED_DEMO_DATA=true).")
    return u


def _ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


def _run(db, user, hours_ago: float, *, execute: bool = True, **body) -> m.Run:
    """POST /runs, then run the job synchronously (as the background worker would)."""
    bg = BackgroundTasks()
    out = R.create_run(R.RunIn(**body), bg, db, user)
    if execute:
        for t in bg.tasks:
            t.func(*t.args, **t.kwargs)
    run = db.get(m.Run, out["id"])
    db.refresh(run)
    _stamp(run, hours_ago)
    db.commit()
    return run


def _stamp(obj, hours_ago: float, duration_min: float = 2.0) -> None:
    obj.created_at = _ago(hours_ago)
    if hasattr(obj, "updated_at"):
        obj.updated_at = obj.created_at + timedelta(minutes=duration_min)


def _stop_part_way(db, run: m.Run, at_step: int, status: str, items_done: int = 1, error: str | None = None) -> None:
    """Leave a run Paused or Failed part-way through, exactly as the engine would (resumable from that step)."""
    steps = [{**s, "items": [dict(i) for i in s["items"]]} for s in run.steps]
    activity = []
    for i, s in enumerate(steps):
        if i < at_step:
            s["status"] = "done"
            for it in s["items"]:
                it["status"] = "done"
                activity.append(it["label"])
        elif i == at_step:
            s["status"] = "failed" if status == "Failed" else "active"
            for k, it in enumerate(s["items"]):
                if k < items_done:
                    it["status"] = "done"
                    activity.append(it["label"])
                elif k == items_done and status == "Failed":
                    it["status"] = "failed"
    run.steps = steps
    run.status, run.step_index, run.error = status, at_step, error
    run.step_label = (f"Failed at step {at_step + 1} - {error}" if status == "Failed"
                      else f"Step {at_step + 1} of {len(steps)} - {steps[at_step]['name']}")
    run.result = {"_work": {"activity": activity[-30:]}}


def _save(db, user, run: m.Run, hours_ago: float, folder: str = "", title: str | None = None) -> m.WorkspaceItem:
    run_created = run.created_at
    item = db.get(m.WorkspaceItem, R.save_run(run.id, R.SaveIn(folder=folder, title=title), db, user)["item_id"])
    run.updated_at = run_created + timedelta(minutes=2)   # saving touches the run; keep its original time
    _stamp(item, hours_ago, 1)
    for v in item.versions:
        v.created_at = item.created_at
    db.commit()
    return item


def _note(db, user, item: m.WorkspaceItem, text: str, hours_ago: float) -> None:
    W.add_note(item.id, W.NoteIn(text=text), db, user)
    item.notes[-1].created_at = _ago(hours_ago)
    db.commit()


def seed_samples(db) -> bool:
    """Returns False when the samples are already present."""
    swanik = _user(db, "swanik@example.com")
    if db.scalar(select(m.Run).where(m.Run.user_id == swanik.id, m.Run.question == MARKER)):
        return False
    maria, sam = _user(db, "maria.chen@example.com"), _user(db, "sam.patel@example.com")
    first_notification = db.scalar(select(m.Notification.id).order_by(m.Notification.id.desc())) or 0

    # ---------------- AI Scientific Search
    a1 = _run(db, swanik, 2.0, module="ai", question=AI[0])
    a1b = _run(db, swanik, 1.8, module="ai", question=AI[3], parent_run_id=a1.id)           # follow-up turn
    a2 = _run(db, swanik, 26, module="ai", question=AI[6], deep_research=True)               # Deep Research
    a3 = _run(db, swanik, 74, module="ai", question=AI[8])
    _run(db, swanik, 98, module="ai", question="What will the weather be in Paris tomorrow?")  # out of scope
    _run(db, swanik, 20 * 24, module="ai", question=AI[1])                                   # older than 7 days
    _run(db, swanik, 40 * 24, module="ai", question=AI[4])                                   # older than 30 days
    failed = _run(db, swanik, 0.5, execute=False, module="ai", question=AI[7])
    _stop_part_way(db, failed, at_step=1, status="Failed", items_done=2, error="Citeline Trialtrove connector timed out")

    # ---------------- Drug Pipeline Explorer
    p1 = _run(db, swanik, 5, module="pipeline", question=PIPE[0], filters={"indication": "SLE"})
    _run(db, swanik, 4.8, module="pipeline", question=PIPE[6], parent_run_id=p1.id)
    _run(db, swanik, 6 * 24, module="pipeline", question="", filters={"indication": "AD", "stage": "Phase 2"})
    _run(db, swanik, 8 * 24, module="pipeline", question=PIPE[2], more_filters={"status": "rec", "phase": ["p1", "p2"]})
    paused_p = _run(db, swanik, 3, execute=False, module="pipeline", question=PIPE[7])
    _stop_part_way(db, paused_p, at_step=3, status="Paused")

    # ---------------- Competitive Insights
    c1 = _run(db, swanik, 25, module="cioa", question=CI[0], workflow="competitive", indication="SLE",
              therapeutic_area="Rheumatology", molecular_target="IRAK4 inhibitor")
    R.approve(c1.id, db, swanik)
    _stamp(c1, 25)
    bg = BackgroundTasks()
    c1b_id = R.change_workflow(c1.id, R.WorkflowIn(workflow="landscape"), bg, db, swanik)["id"]
    for t in bg.tasks:
        t.func(*t.args, **t.kwargs)
    c1b = db.get(m.Run, c1b_id)
    db.refresh(c1b)
    _stamp(c1b, 20)
    _run(db, swanik, 9 * 24, module="cioa", question=CI[1], workflow="endpoint")
    paused_c = _run(db, swanik, 50, execute=False, module="cioa", question=CI[2], workflow="epidemiology",
                    therapeutic_area="Dermatology", indication="AD")
    _stop_part_way(db, paused_c, at_step=2, status="Paused", items_done=6)
    db.commit()

    # ---------------- Workspace: own items, folders, versions, notes, sharing, archive
    i1 = _save(db, swanik, a1, 1.7, folder="Scientific Evidence", title="Role of IRAK4 in SLE, RA and AD")
    i1.versions.insert(0, m.ItemVersion(label="v2", note="Added follow-up on evidence gaps", snapshot=i1.snapshot,
                                        created_at=_ago(1.6)))
    i2 = _save(db, swanik, a2, 25, folder="Competitive Intelligence", title="IRAK4 versus JAK, TYK2, BTK and cytokine therapies")
    W.share(i2.id, W.ShareIn(user_id=sam.id, permission="view"), db, swanik)
    _note(db, sam, i2, "Useful comparison. Can we add payer access for the JAK class?", 22)
    _note(db, swanik, i2, "Good idea, I will add it in the next run.", 21)
    i3 = _save(db, swanik, a3, 70, folder="Scientific Evidence")
    W.patch_item(i3.id, W.ItemPatch(archived=True), db, swanik)                              # Archived tab
    _stamp(i3, 70, 1)
    _save(db, swanik, p1, 4.5, folder="Pipeline Reviews", title="IRAK4 pipeline - SLE")
    _save(db, swanik, c1, 24, folder="Competitive Intelligence", title="Competitive Opportunity Report - IRAK4")

    # items other people share with Swanik: directly, and through the Clinical group (Swanik is a member)
    m1 = _run(db, maria, 48, module="ai", question=AI[2])
    mi1 = _save(db, maria, m1, 47, folder="Scientific Evidence")
    clinical = db.scalar(select(m.Group).where(m.Group.name == "Clinical"))
    W.share(mi1.id, W.ShareIn(group_id=clinical.id, permission="edit"), db, maria)
    _note(db, maria, mi1, "Shared with the Clinical team for review before Friday.", 46)
    m2 = _run(db, maria, 30, module="pipeline", question=PIPE[3], filters={"indication": "RA"})
    mi2 = _save(db, maria, m2, 29, folder="Pipeline Reviews")
    W.share(mi2.id, W.ShareIn(user_id=swanik.id, permission="view"), db, maria)

    # ---------------- Prioritization Scoring
    model = db.scalar(select(m.ScoringModel).where(m.ScoringModel.status == "Published"))
    aid = SC.create_assessment(SC.NewAssessment(model_id=model.id, title="IRAK4 Indication Prioritisation"), db, swanik)["id"]
    SC.set_override(aid, SC.OverrideIn(indication="RA", criterion="population", score=4,
                                       justification="Claims data show a larger biologic-experienced population than the AI estimate."),
                    db, swanik)
    SC.set_override(aid, SC.OverrideIn(indication="AD", criterion="headroom", score=1,
                                       justification="Two oral JAK inhibitors and several biologics are now approved in AD."),
                    db, swanik)
    SC.save_scenario(aid, SC.ScenarioIn(name="Baseline"), db, swanik)
    keys = [c["key"] for c in db.get(m.Assessment, aid).criteria]
    payer = dict(zip(keys, [20, 20, 20, 30, 10]))
    SC.apply_weights(aid, SC.WeightsIn(weights=payer), db, swanik)
    SC.save_scenario(aid, SC.ScenarioIn(name="Payer-weighted"), db, swanik)
    SC.apply_weights(aid, SC.WeightsIn(weights={k: 20 for k in keys}), db, swanik)
    si = db.get(m.WorkspaceItem, SC.to_workspace(aid, db, swanik)["item_id"])
    si.folder = "IRAK4 Indication Prioritisation"
    _stamp(si, 6, 1)
    assessment = db.get(m.Assessment, aid)
    _stamp(assessment, 7, 60)
    for k, sc in enumerate(assessment.scenarios):
        sc.created_at = _ago(6.5 - k * 0.2)

    # ---------------- Notifications: spread over time; older than a day are read
    notes = db.scalars(select(m.Notification).where(m.Notification.id > first_notification)
                       .order_by(m.Notification.id)).all()
    per_user: dict[int, int] = {}
    for n in reversed(notes):  # newest first, counted per recipient: each user's 4 newest stay unread
        k = per_user[n.user_id] = per_user.get(n.user_id, -1) + 1
        n.created_at = _ago(0.4 + k * 9)
        n.unread = k < 4
    db.commit()
    return True


def main() -> None:
    from app.db.base import SessionLocal, engine
    from app.db.upgrade import upgrade_schema
    from app.seed import seed, seed_reference

    logging.basicConfig(level=logging.WARNING)
    upgrade_schema(engine)
    with SessionLocal() as db:
        seed(db)
        seed_reference(db)
        loaded = seed_samples(db)
    print("Sample data loaded. Log in as swanik@example.com / ChangeMe123!" if loaded else
          "Sample data is already present. To start over: stop the API, delete gilead.db, run this again.")


if __name__ == "__main__":
    main()
