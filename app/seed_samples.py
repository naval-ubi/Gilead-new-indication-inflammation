"""Sample data for frontend development and testing: fills every screen with realistic records.

Everything is created through the same functions the API endpoints use, so each record has exactly the shape the
frontend receives in real use. Timestamps are spread over the last ~6 weeks so History date filters have data.

No AI-written content: sample runs never call the AI Scientific Search service (it is billed), so their AI text
fields (pipeline summaries, report takeaways and sections) are empty. Counts, tables and charts are real database
content. There are no sample AI Search runs; create those by asking a question with the service configured.

What you get (log in as swanik@example.com / ChangeMe123!):
  Home / History  completed pipeline answers and reports; runs in progress (one recent, two stalled and resumable),
                  a failed and two paused runs (Resume / Retry); an out-of-scope question; runs older than 7 and
                  30 days; saved items across modules
  Conversations   Pipeline and Competitive Insights threads with a follow-up and a changed workflow
  Workspace       own items in folders, an archived item, a version history, notes, an item shared with a
                  colleague, items shared with you directly and through your group
  Scoring         an assessment with two justified scores and two saved scenarios; a draft whose weights do not
                  total 100% (error state). AI scores show as Missing until the AI service can score.
  Pipeline        programs at every stage incl. Filed, Marketed, On hold and Discontinued, in five indications
  Admin           more colleagues (share dialog, user list), an extra group, audit history with before/after
  Notifications   read and unread

Databases holding samples from earlier versions (placeholder AI text, made-up AI scores) are cleaned first.

Run:  python -m app.seed_samples        (safe to repeat; to start over, delete gilead.db and start the API)
Or set SEED_SAMPLE_DATA=true in .env to load it on start-up.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks
from sqlalchemy import select, update

from app.db import models as m
from app.routers import admin as AD
from app.routers import runs as R
from app.routers import scoring as SC
from app.routers import workspace as W
from app.services.catalog import CIOA_EXAMPLE_QUESTIONS as CI

log = logging.getLogger(__name__)
PIPE = [  # questions a user might ask in the Drug Pipeline Explorer
    "Which IRAK4 inhibitors and degraders are in development for SLE, RA and AD?",
    "Which companies are developing IRAK4-targeting therapies, and how do their assets compare?",
    "Which IRAK4 programs have been discontinued or put on hold, and why?",
    "Map IRAK4-targeting drug candidates across development stages for SLE, RA and AD.",
    "What efficacy results have been reported for IRAK4 inhibitors in rheumatoid arthritis?",
    "Which IRAK4 programs are recruiting patients with atopic dermatitis?",
    "Which IRAK4 programs target hidradenitis suppurativa or psoriasis?",
]
MARKER = PIPE[0]     # part 1 is loaded when Swanik has asked this question
MARKER_2 = PIPE[6]   # part 2 (runs in progress, more saved items, pipeline, admin)
NOW = datetime.now(timezone.utc)

# How samples from earlier versions are recognised (for clean-up only; never shown).
OLD_PLACEHOLDER_MARKERS = ["Sample content for testing", "Sample text for:", "sample finding relevant to", "[stub-llm]"]
OLD_AI_SAMPLE_QUESTIONS = {
    "What role does IRAK4 play in the pathogenesis of SLE, RA, and AD, and which inflammatory pathways are shared or disease-specific?",
    "What genetic, molecular, preclinical, and clinical evidence supports IRAK4 as a therapeutic target in SLE, RA, and AD?",
    "How does IRAK4 inhibition affect TLR/IL-1R–MyD88 signaling, inflammatory cytokine production, and immune-cell activation in each disease?",
    "Which of the three diseases has the strongest scientific evidence supporting IRAK4 inhibition, and what are the major evidence gaps?",
    "Which biomarkers could predict patient response to IRAK4 inhibitors or demonstrate target engagement in SLE, RA, and AD?",
    "What clinical trials have evaluated IRAK4 inhibitors or degraders in SLE, RA, and AD, and what efficacy and safety findings have been reported?",
    "How does IRAK4 inhibition compare with JAK, TYK2, BTK, and cytokine-targeted therapies in biological rationale, efficacy evidence, and safety?",
    "Which patient subgroups or disease phenotypes in SLE, RA, and AD are most likely to benefit from IRAK4 inhibition, based on available evidence?",
    "What are the potential safety risks of long-term IRAK4 inhibition, particularly infection risk, immune suppression, and off-target effects?",
    "What will the weather be in Paris tomorrow?",
}


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
    """Leave a run In Progress, Paused or Failed part-way through, exactly as the engine would (resumable)."""
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
                elif k == items_done and status == "In Progress":
                    it["status"] = "active"
    run.steps = steps
    run.status, run.step_index, run.error = status, at_step, error
    run.step_label = (f"Failed at step {at_step + 1} - {error}" if status == "Failed"
                      else f"Step {at_step + 1} of {len(steps)} - {steps[at_step]['name']}")
    run.result = {"_work": {"activity": activity[-30:]}}
    run.updated_at = run.created_at + timedelta(minutes=3)   # last progress shortly after it started


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


def _has_run(db, user: m.User, question: str) -> bool:
    return db.scalar(select(m.Run.id).where(m.Run.user_id == user.id, m.Run.question == question).limit(1)) is not None


def _has_assessment(db, user: m.User, title: str) -> bool:
    return db.scalar(select(m.Assessment.id).where(m.Assessment.owner_id == user.id, m.Assessment.title == title)
                     .limit(1)) is not None


def _find(db, user: m.User, question: str, **where) -> m.Run | None:
    q = select(m.Run).where(m.Run.user_id == user.id, m.Run.question == question, m.Run.status == "Completed")
    for k, v in where.items():
        q = q.where(getattr(m.Run, k) == v)
    return db.scalars(q.order_by(m.Run.id)).first()


def _soften_notifications(db, after_id: int, unread: int = 3) -> None:
    """Spread new notifications over time; each recipient's newest few stay unread."""
    notes = db.scalars(select(m.Notification).where(m.Notification.id > after_id).order_by(m.Notification.id)).all()
    per_user: dict[int, int] = {}
    for n in reversed(notes):
        k = per_user[n.user_id] = per_user.get(n.user_id, -1) + 1
        n.created_at = _ago(0.3 + k * 8)
        n.unread = k < unread


# ------------------------------------------------------------------ clean-up of earlier sample versions
def remove_placeholder_samples(db) -> int:
    """Delete sample records from earlier versions that hold placeholder AI text: the old AI Search sample runs,
    any run whose result contains placeholder text, and the workspace items saved from them. Clears made-up AI
    scores on assessments (the AI service cannot score yet, so any stored AI score is a placeholder).
    Returns the number of runs removed."""
    demo_ids = [u.id for u in db.scalars(select(m.User).where(m.User.email.in_(
        ["swanik@example.com", "maria.chen@example.com"])))]
    doomed = []
    for r in db.scalars(select(m.Run).where(m.Run.user_id.in_(demo_ids))):
        if r.module == "ai" and r.question in OLD_AI_SAMPLE_QUESTIONS and (r.result or {}).get("provider") is None:
            doomed.append(r.id)
        elif any(k in json.dumps(r.result or {}, default=str) for k in OLD_PLACEHOLDER_MARKERS):
            doomed.append(r.id)
    for it in db.scalars(select(m.WorkspaceItem)):
        if it.run_id in doomed or any(k in json.dumps(it.snapshot or {}, default=str) for k in OLD_PLACEHOLDER_MARKERS):
            db.delete(it)                                       # versions, shares and notes go with it
    db.flush()
    if doomed:
        db.execute(update(m.Run).where(m.Run.parent_run_id.in_(doomed)).values(parent_run_id=None))
        for r in db.scalars(select(m.Run).where(m.Run.id.in_(doomed))):
            db.delete(r)
    for a in db.scalars(select(m.Assessment)):
        if any(v is not None for scores in (a.ai_scores or {}).values() for v in (scores or {}).values()):
            a.ai_scores = {ind: {k: None for k in (scores or {})} for ind, scores in a.ai_scores.items()}
    db.commit()
    return len(doomed)


def seed_samples(db) -> bool:
    """Applies every part that is not loaded yet. Returns False when everything was already present."""
    from app.services.scientific_search import offline

    removed = remove_placeholder_samples(db)
    if removed:
        log.info("Removed %d sample runs with placeholder content", removed)
    swanik = _user(db, "swanik@example.com")
    loaded = False
    with offline():  # sample runs never call the billed AI service; their AI text fields stay empty
        if not _has_run(db, swanik, MARKER):
            _part_1(db, swanik)
            loaded = True
        if not _has_run(db, swanik, MARKER_2):
            _part_2(db, swanik)
            loaded = True
    return loaded or bool(removed)


def _part_1(db, swanik: m.User) -> None:
    """Completed, failed and paused runs, conversations, workspace, sharing, scoring, notifications."""
    maria, sam = _user(db, "maria.chen@example.com"), _user(db, "sam.patel@example.com")
    first_notification = db.scalar(select(m.Notification.id).order_by(m.Notification.id.desc())) or 0

    # ---------------- Drug Pipeline Explorer
    p1 = _run(db, swanik, 5, module="pipeline", question=PIPE[0], filters={"indication": "SLE"})
    _run(db, swanik, 4.8, module="pipeline", question=PIPE[1], parent_run_id=p1.id)          # follow-up turn
    _run(db, swanik, 6 * 24, module="pipeline", question="", filters={"indication": "AD", "stage": "Phase 2"})
    p3 = _run(db, swanik, 8 * 24, module="pipeline", question=PIPE[3], more_filters={"status": "rec", "phase": ["p1", "p2"]})
    p_old = _run(db, swanik, 20 * 24, module="pipeline", question=PIPE[4], filters={"indication": "RA"})  # > 7 days
    _run(db, swanik, 40 * 24, module="pipeline", question=PIPE[5], filters={"indication": "AD"})         # > 30 days
    _run(db, swanik, 98, module="pipeline", question="What will the weather be in Paris tomorrow?")      # out of scope
    paused_p = _run(db, swanik, 3, execute=False, module="pipeline", question=PIPE[2])
    _stop_part_way(db, paused_p, at_step=3, status="Paused")
    failed = _run(db, swanik, 0.5, execute=False, module="pipeline", question=PIPE[1], filters={"modality": "Monoclonal antibody"})
    _stop_part_way(db, failed, at_step=1, status="Failed", items_done=0,
                   error="The scientific search service did not answer within 90 seconds")

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
    i1 = _save(db, swanik, p1, 4.5, folder="Pipeline Reviews", title="IRAK4 pipeline - SLE")
    i1.versions.insert(0, m.ItemVersion(label="v2", note="Updated after the Phase 2 filter review", snapshot=i1.snapshot,
                                        created_at=_ago(4.2)))
    i2 = _save(db, swanik, c1, 24, folder="Competitive Intelligence", title="Competitive Opportunity Report - IRAK4")
    W.share(i2.id, W.ShareIn(user_id=sam.id, permission="view"), db, swanik)
    _note(db, sam, i2, "Useful overview. Can we add payer access for the JAK class?", 22)
    _note(db, swanik, i2, "Good idea, I will add it in the next run.", 21)
    i3 = _save(db, swanik, p_old, 20 * 24 - 1, folder="Pipeline Reviews", title="IRAK4 in RA - efficacy review")
    W.patch_item(i3.id, W.ItemPatch(archived=True), db, swanik)                              # Archived tab
    _stamp(i3, 20 * 24 - 1, 1)
    _save(db, swanik, p3, 8 * 24 - 1, folder="Pipeline Reviews", title="Recruiting Phase 1-2 programs")

    # items other people share with Swanik: through the Clinical group (Swanik is a member), and directly
    m1 = _run(db, maria, 48, module="pipeline", question=PIPE[4], filters={"indication": "RA"})
    mi1 = _save(db, maria, m1, 47, folder="Pipeline Reviews", title="IRAK4 programs in RA")
    clinical = db.scalar(select(m.Group).where(m.Group.name == "Clinical"))
    W.share(mi1.id, W.ShareIn(group_id=clinical.id, permission="edit"), db, maria)
    _note(db, maria, mi1, "Shared with the Clinical team for review before Friday.", 46)
    m2 = _run(db, maria, 30, module="cioa", question=CI[2], workflow="landscape", indication="AD")
    mi2 = _save(db, maria, m2, 29, folder="Competitive Intelligence", title="Therapeutic landscape - AD")
    W.share(mi2.id, W.ShareIn(user_id=swanik.id, permission="view"), db, maria)

    # ---------------- Prioritization Scoring (AI scores start as Missing; these two are entered by the user)
    if _has_assessment(db, swanik, "IRAK4 Indication Prioritisation"):   # kept from an earlier sample version
        _soften_notifications(db, first_notification, unread=4)
        db.commit()
        return
    model = db.scalar(select(m.ScoringModel).where(m.ScoringModel.status == "Published"))
    aid = SC.create_assessment(SC.NewAssessment(model_id=model.id, title="IRAK4 Indication Prioritisation"), db, swanik)["id"]
    SC.set_override(aid, SC.OverrideIn(indication="RA", criterion="population", score=4,
                                       justification="Claims data show a large biologic-experienced population."),
                    db, swanik)
    SC.set_override(aid, SC.OverrideIn(indication="AD", criterion="headroom", score=1,
                                       justification="Two oral JAK inhibitors and several biologics are approved in AD."),
                    db, swanik)
    SC.save_scenario(aid, SC.ScenarioIn(name="Baseline"), db, swanik)
    keys = [c["key"] for c in db.get(m.Assessment, aid).criteria]
    SC.apply_weights(aid, SC.WeightsIn(weights=dict(zip(keys, [20, 20, 20, 30, 10]))), db, swanik)
    SC.save_scenario(aid, SC.ScenarioIn(name="Payer-weighted"), db, swanik)
    SC.apply_weights(aid, SC.WeightsIn(weights={k: 20 for k in keys}), db, swanik)
    si = db.get(m.WorkspaceItem, SC.to_workspace(aid, db, swanik)["item_id"])
    si.folder = "IRAK4 Indication Prioritisation"
    _stamp(si, 6, 1)
    assessment = db.get(m.Assessment, aid)
    _stamp(assessment, 7, 60)
    for k, sc in enumerate(assessment.scenarios):
        sc.created_at = _ago(6.5 - k * 0.2)

    _soften_notifications(db, first_notification, unread=4)
    db.commit()


EXTRA_PROGRAMS = [  # name, sponsor, modality, target, indication, stage, activity, geography
    ("Asset K", "Sponsor 10", "Targeted protein degrader", "IRAK4 degrader", "Hidradenitis suppurativa", "Phase 2", "Recruiting", "Global"),
    ("Asset L", "Sponsor 11", "Monoclonal antibody", "IL-1R pathway antibody", "AD", "Filed", "Active", "US"),
    ("Asset M", "Sponsor 12", "Small molecule", "JAK1", "RA", "Marketed", "Active", "Global"),
    ("Asset N", "Sponsor 13", "Small molecule", "TLR7", "SLE", "Phase 3", "Recruiting", "EU"),
    ("Asset O", "Sponsor 14", "Small molecule", "BTK", "SLE", "Phase 2", "On hold", "US"),
    ("Asset P", "Sponsor 7", "Small molecule", "IRAK4 inhibitor", "Psoriasis", "Phase 1", "Withdrawn", "EU"),
    ("Asset Q", "Sponsor 15", "Small molecule", "TYK2", "Psoriasis", "Marketed", "Active", "Global"),
    ("Asset A", "Sponsor 1", "Small molecule", "IRAK4 inhibitor", "AD", "Preclinical", "—", "US"),
    ("Asset K", "Sponsor 10", "Targeted protein degrader", "IRAK4 degrader", "AD", "Phase 1", "Recruiting", "Global"),
]
EXTRA_USERS = [  # name, email, role, groups, status
    ("Priya Nair", "priya.nair@example.com", "Analyst", ["Commercial"], "Active"),
    ("David Okafor", "david.okafor@example.com", "Analyst", ["R&D", "Clinical"], "Active"),
    ("Elena Rossi", "elena.rossi@example.com", "Reviewer", ["Market Access"], "Active"),
    ("Tom Becker", "tom.becker@example.com", "Viewer", ["Commercial"], "Inactive"),
]


def _part_2(db, swanik: m.User) -> None:
    """Runs in progress, more saved items, pipeline programs at every stage, colleagues and admin history."""
    from app.seed import _get_or_create

    maria = _user(db, "maria.chen@example.com")
    first_notification = db.scalar(select(m.Notification.id).order_by(m.Notification.id.desc())) or 0

    # ---------------- pipeline programs: every stage and status, more indications
    for name, sponsor, modality, target, ind, stage, activity, geo in EXTRA_PROGRAMS:
        sp = _get_or_create(db, m.Sponsor, name=sponsor)
        asset = db.scalar(select(m.DrugAsset).filter_by(name=name, sponsor_id=sp.id)) or m.DrugAsset(
            name=name, sponsor=sp, modality=_get_or_create(db, m.Modality, name=modality),
            target=_get_or_create(db, m.Target, name=target))
        indication = _get_or_create(db, m.Indication, code=ind)
        if asset.id is None or not db.scalar(select(m.PipelineProgram).filter_by(
                asset_id=asset.id, indication_id=indication.id, geography=geo)):
            db.add(m.PipelineProgram(asset=asset, indication=indication, stage=stage, activity=activity, geography=geo))
            db.flush()
    db.commit()

    # ---------------- colleagues and admin history (audit log with before/after)
    for name, email, role, groups, status in EXTRA_USERS:
        if not db.scalar(select(m.User).where(m.User.email == email)):
            AD.create_user(AD.UserIn(name=name, email=email, role=role, groups=groups, status=status,
                                     password="ChangeMe123!"), db, maria)
    if not db.scalar(select(m.Group).where(m.Group.name == "Oncology")):
        AD.create_group(AD.GroupIn(name="Oncology", description="Oncology indications team"), db, maria)
    elena = _user(db, "elena.rossi@example.com")
    AD.update_user(elena.id, AD.UserIn(name=elena.name, email=elena.email, role="Reviewer",
                                       groups=["Market Access", "Oncology"], status="Active"), db, maria)
    priya = _user(db, "priya.nair@example.com")
    for k, a in enumerate(db.scalars(select(m.AuditLog).where(m.AuditLog.user_id == maria.id)
                                     .order_by(m.AuditLog.id.desc()).limit(10)).all()):
        a.created_at = _ago(30 + k * 20)

    # ---------------- runs in progress: one recent, two stalled (no worker; Resume continues them)
    ip_p = _run(db, swanik, 0.08, execute=False, module="pipeline", question=MARKER_2)
    _stop_part_way(db, ip_p, at_step=2, status="In Progress", items_done=1)
    _stamp(ip_p, 0.08, 4)                                     # updated 1 minute ago: looks live when loaded
    ip_p2 = _run(db, swanik, 1.5, execute=False, module="pipeline", question=PIPE[5], filters={"indication": "AD"})
    _stop_part_way(db, ip_p2, at_step=1, status="In Progress", items_done=0)
    ip_c = _run(db, swanik, 6, execute=False, module="cioa", question=CI[2], workflow="unmet_need",
                therapeutic_area="Autoimmune and inflammatory disease", molecular_target="IRAK4 degrader")
    _stop_part_way(db, ip_c, at_step=2, status="In Progress", items_done=4)
    db.commit()

    # ---------------- more saved items (Home "Saved Searches"), across modules and folders
    c1b = _find(db, swanik, CI[0], workflow="landscape")
    if c1b:
        _save(db, swanik, c1b, 19, folder="Competitive Intelligence", title="Therapeutic landscape - IRAK4 in SLE")
    ce = _find(db, swanik, CI[1], workflow="endpoint")
    if ce:
        it = _save(db, swanik, ce, 9 * 24 - 1, folder="Clinical Endpoints", title="Endpoint benchmarks - IRAK4 programs")
        W.share(it.id, W.ShareIn(user_id=priya.id, permission="edit"), db, swanik)
        _note(db, priya, it, "I added the AD endpoints from the latest Phase 2 readouts.", 9 * 24 - 3)
    pl = _run(db, swanik, 12, module="pipeline", question="", filters={"stage": "Marketed"})
    _save(db, swanik, pl, 11.5, folder="Pipeline Reviews", title="Marketed competitors")
    commercial = db.scalar(select(m.Group).where(m.Group.name == "Commercial"))
    lp = db.scalar(select(m.WorkspaceItem).where(m.WorkspaceItem.run_id == pl.id))
    W.share(lp.id, W.ShareIn(group_id=commercial.id, permission="view"), db, swanik)

    # ---------------- draft assessment whose weights do not total 100% (error state in Scoring)
    if not _has_assessment(db, swanik, "IRAK4 - payer access view (draft)"):
        model = db.scalar(select(m.ScoringModel).where(m.ScoringModel.status == "Published"))
        aid = SC.create_assessment(SC.NewAssessment(model_id=model.id, title="IRAK4 - payer access view (draft)"), db, swanik)["id"]
        SC.remove_criterion(aid, "pricing", db, swanik)      # weights now total 80% -> weight_errors
        draft = db.get(m.WorkspaceItem, SC.to_workspace(aid, db, swanik)["item_id"])
        draft.folder = "IRAK4 Indication Prioritisation"
        _stamp(draft, 2.5, 1)
        _stamp(db.get(m.Assessment, aid), 3, 30)

    _soften_notifications(db, first_notification)
    db.commit()


def main() -> None:
    from app.db.base import SessionLocal, engine
    from app.db.migrate import prepare_database
    from app.seed import seed, seed_reference

    logging.basicConfig(level=logging.WARNING)
    prepare_database(engine)
    with SessionLocal() as db:
        seed(db)
        seed_reference(db)
        loaded = seed_samples(db)
    print("Sample data loaded. Log in as swanik@example.com / ChangeMe123!" if loaded else
          "Sample data is already present. To start over: stop the API, delete gilead.db, run this again.")


if __name__ == "__main__":
    main()
