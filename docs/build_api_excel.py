"""Builds docs/API_Handover.xlsx: every endpoint with its screen, permission, description, and a real sample request
and response. Samples come from calling the API against a new temporary database loaded with the sample data, so
they match what the frontend receives. gilead.db is not used or modified.

The AI Scientific Search service is not called (it is billed): AI answers in the samples are a real reply recorded
from the service (tests/fixtures/ai_search_query.json), served through a local stand-in.

Usage (from the repo root; needs the project requirements and openpyxl):
    PYTHONPATH=. python docs/build_api_excel.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "API_Handover.xlsx"
TMP = Path(tempfile.mkdtemp()) / "handover.db"
AI_BASE = "https://ai-search.local"
os.environ.update(DATABASE_URL=f"sqlite:///{TMP}", SEED_DEMO_DATA="true", SEED_SAMPLE_DATA="true",
                  AI_SEARCH_BASE_URL=AI_BASE)

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import models as m  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.services import catalog  # noqa: E402
from app.services import pipeline as pl  # noqa: E402
from app.services import scientific_search as ss  # noqa: E402

import httpx  # noqa: E402

RECORDED = json.loads((ROOT / "tests" / "fixtures" / "ai_search_query.json").read_text())


def recorded_service(request: httpx.Request) -> httpx.Response:
    """Local stand-in for the AI service: the recorded reply for every query, a fixed rephrasing for rephrase."""
    if request.url.path == "/health":
        return httpx.Response(200, json={"status": "healthy", "service": "scientific-search-api", "message": "ok"})
    if request.url.path.endswith("/rephrase"):
        q = json.loads(request.content)["query"]
        return httpx.Response(200, json={"status": "success", "original_query": q, "suggested_query":
                                         "Which biomarkers can show IRAK4 target engagement in patients with SLE, RA or AD?"})
    return httpx.Response(200, json=RECORDED)


ss._client = lambda: httpx.Client(base_url=AI_BASE, transport=httpx.MockTransport(recorded_service))

API = "/api/v1"
ROWS: list[dict] = []
SEEN: set[tuple[str, str]] = set()


# ------------------------------------------------------------------ helpers
def trim(v, depth=0):
    """Keep samples readable: at most 2 list items (+ a note), long strings shortened, deep nesting summarised."""
    if isinstance(v, list):
        out = [trim(x, depth + 1) for x in v[:2]]
        if len(v) > 2:
            out.append(f"... {len(v) - 2} more")
        return out
    if isinstance(v, dict):
        if depth > 5:
            return "{...}"
        return {k: trim(x, depth + 1) for k, x in v.items()}
    if isinstance(v, str) and len(v) > 220:
        return v[:200] + " ... (truncated)"
    return v


def js(v) -> str:
    return json.dumps(trim(v), indent=2, ensure_ascii=False, default=str)


def call(c, method, path, h, *, params=None, body=None, module, screen, desc, notes="", template=None, record=True,
         prefix=API):
    r = c.request(method, prefix + path, headers=h, params=params, json=body)
    if record:
        tpl = template or path
        SEEN.add((method, prefix + tpl))
        ctype = r.headers.get("content-type", "")
        if r.status_code == 204 or not r.content:
            resp = "(no content)"
        elif "json" in ctype:
            resp = js(r.json())
        else:
            text = r.text
            resp = text[:1500] + (" ... (truncated)" if len(text) > 1500 else "")
        q = ("?" + "&".join(f"{k}={v}" for k, v in params.items() if v is not None)) if params else ""
        ROWS.append({"module": module, "screen": screen, "method": method, "endpoint": prefix + tpl,
                     "perm": PERMS[(method, prefix + tpl)], "desc": desc,
                     "request": (f"{method} {prefix}{path}{q}" + (f"\n\n{js(body)}" if body is not None else "")),
                     "response": resp, "status": r.status_code, "notes": notes})
    if r.status_code >= 400 and record and r.status_code not in EXPECTED_ERRORS:
        raise SystemExit(f"Unexpected {r.status_code} for {method} {path}: {r.text[:300]}")
    return r


def permissions_by_route() -> dict:
    """Permission each route requires: the code passed to require(...), 'login' for any signed-in user, or 'public'."""
    def routes():
        for r in app.router.routes:
            ctx = getattr(r, "include_context", None)
            if ctx is not None:                      # routers added with app.include_router(prefix=...)
                for rt in ctx.included_router.routes:
                    yield ctx.prefix + rt.path, rt
            else:
                yield getattr(r, "path", ""), r

    def calls(dep):
        for d in dep.dependencies:
            yield d.call
            yield from calls(d)

    out = {}
    for path, r in routes():
        dep = getattr(r, "dependant", None)
        if dep is None:
            continue
        perm = None
        for fn in calls(dep):
            if getattr(fn, "__name__", "") == "dep" and fn.__closure__:
                perm = next((cc.cell_contents for cc in fn.__closure__ if isinstance(cc.cell_contents, str)), perm)
        signed_in = any(getattr(fn, "__name__", "") in ("current_user", "token_claims") for fn in calls(dep))
        for meth in (r.methods or set()) - {"HEAD"}:
            out[(meth, path)] = perm or ("login" if signed_in else "public")
    return out


EXPECTED_ERRORS = {409, 422}  # documented error examples


# ------------------------------------------------------------------ capture
with TestClient(app) as c:
    PERMS = permissions_by_route()

    def login(email):
        return {"Authorization": "Bearer " + c.post(API + "/auth/login",
                                                    json={"email": email, "password": "ChangeMe123!"}).json()["access_token"]}

    H, A = login("swanik@example.com"), login("maria.chen@example.com")
    with SessionLocal() as db:
        sw = db.scalar(select(m.User).where(m.User.email == "swanik@example.com"))
        runs = db.scalars(select(m.Run).where(m.Run.user_id == sw.id, m.Run.deleted.is_(False)).order_by(m.Run.id)).all()
        live = max((r for r in runs if r.status == "In Progress"), key=lambda r: r.created_at).id
        # the sample "live" run is only live for 15 minutes after loading; refresh it in this temporary copy so the
        # loader example shows a healthy in-progress run (stalled=false)
        from datetime import datetime, timedelta, timezone
        db.get(m.Run, live).updated_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        stalled = next(r.id for r in runs if r.status == "In Progress" and r.module == "cioa")
        item = db.scalar(select(m.WorkspaceItem).where(m.WorkspaceItem.owner_id == sw.id,
                                                       m.WorkspaceItem.title == "IRAK4 pipeline - SLE"))
        item_id, version_id = item.id, item.versions[-1].id
        notif = db.scalar(select(m.Notification.id).where(m.Notification.user_id == sw.id).order_by(m.Notification.id.desc()))
        priya = db.scalar(select(m.User.id).where(m.User.email == "priya.nair@example.com"))
        model_id = db.scalar(select(m.ScoringModel.id).where(m.ScoringModel.status == "Published"))
        connector = db.scalar(select(m.Connector.id).where(m.Connector.name == "PubMed"))
        prompt_id = db.scalar(select(m.AdminRecord.id).where(m.AdminRecord.kind == "prompt"))
        role_viewer = db.scalar(select(m.Role.id).where(m.Role.name == "Viewer"))

    # an AI Search conversation for the samples below (answers are the recorded service reply)
    first = c.post(API + "/runs", headers=H, json={"module": "ai", "question": "What role does IRAK4 play in SLE?"}).json()
    ai_done = c.post(API + "/runs", headers=H, json={"module": "ai", "question": "Which biomarkers show IRAK4 target engagement?",
                                                     "parent_run_id": first["id"]}).json()["id"]

    # ---- Account & platform
    mod = "Account & platform"
    call(c, "POST", "/auth/login", None, body={"email": "swanik@example.com", "password": "ChangeMe123!"}, module=mod,
         screen="Sign-in page", desc="Signs the user in. Store access_token and send it as 'Authorization: Bearer <token>' on every call.",
         notes="401 = wrong email or password, or inactive user.")
    call(c, "GET", "/me", H, module=mod, screen="App start / header avatar",
         desc="Current user with role, permissions (use to show or hide actions), research context and preferences.")
    call(c, "GET", "/me/profile", H, module=mod, screen="Profile menu > My Profile",
         desc="Profile dialog: initials, role, groups, permissions, active session, last login.")
    call(c, "PATCH", "/me/profile", H, body={"name": "Swanik"}, module=mod, screen="My Profile > edit name",
         desc="Changes the display name. Email, role and groups are managed by admins.")
    call(c, "GET", "/context/options", H, module=mod, screen="Research context dialog",
         desc="Options for the context dialog: indications (grouped), geographies, therapeutic areas, molecular targets, module names.")
    call(c, "PUT", "/me/context", H, body={"target": "IRAK4", "mechanism": "IRAK4 inhibition", "indications": ["SLE", "RA", "AD"],
                                            "geography": "United States", "session": "IRAK4 Inflammation Programme"},
         module=mod, screen="Research context dialog > Apply",
         desc="Saves the header research context. Used as the default for every new run.")
    call(c, "PUT", "/me/preferences", H, body={"deep_research": False, "in_app_notifications": True, "email": False},
         module=mod, screen="Preferences dialog > Save", desc="Saves user preferences.")
    call(c, "GET", "/notifications", H, params={"limit": 5}, module=mod, screen="Header bell + badge",
         desc="Latest notifications. 'unread' drives the badge; 'route' says which screen to open on click.")
    call(c, "POST", f"/notifications/{notif}/read", H, template="/notifications/{nid}/read", module=mod,
         screen="Bell > click a notification", desc="Marks one notification as read.")
    call(c, "POST", "/notifications/read-all", H, module=mod, screen="Bell > Mark all read", desc="Marks all notifications as read.")
    call(c, "GET", "/help/content", H, module=mod, screen="Help menu (Docs, Guides, FAQs)",
         desc="Content for the Help menu dialogs and AI Help Chat quick replies.")
    call(c, "POST", "/help/chat", H, body={"message": "How do I share a saved search?"}, module=mod, screen="AI Help Chat",
         desc="Answers a how-to question about the platform.")

    # ---- Home
    mod = "Home"
    call(c, "GET", "/home/summary", H, module=mod, screen="Home KPI cards",
         desc="The three KPI cards (In Progress, Completed, Saved Searches) with counts, plus a short preview list.")
    for tab, label in [("in_progress", "In Progress"), ("recent_results", "Completed"), ("saved", "Saved Searches")]:
        call(c, "GET", "/home", H, params={"tab": tab, "limit": 3}, module=mod, screen=f"Home > {label} card list",
             desc=f"Card list for '{label}'. Optional: module (filter), sort=date|module, desc=true|false, limit.",
             notes={"in_progress": "Rows show progress_pct; stalled=true means no progress for 15 min: show Resume.",
                    "recent_results": "action=view: open the module screen with GET /runs/{id}/thread.",
                    "saved": "Saved items (kind=item). action=run_again: POST /workspace/items/{id}/rerun."}[tab])
    call(c, "POST", f"/home/runs/{ai_done}", H, template="/home/runs/{run_id}", body={"action": "duplicate"}, module=mod,
         screen="Card more-actions > Duplicate / Delete", desc="Duplicates or deletes (soft) a run from a Home card.",
         notes="action: duplicate | delete.")

    # ---- AI Scientific Search
    mod = "AI Scientific Search"
    new_ai = call(c, "POST", "/runs", H, body={"module": "ai", "question": "What role does IRAK4 play in SLE?",
                                                "deep_research": False, "notify": False},
                  module=mod, screen="Ask box > Submit",
                  desc="Starts a research run and returns 202 straight away. Then poll GET /runs/{id} until status is Completed.",
                  notes="Optional: sources [connector names], deep_research, notify, parent_run_id (follow-up).").json()
    call(c, "POST", "/runs", H, body={"module": "ai", "question": "And what about long-term safety?", "parent_run_id": new_ai["id"]},
         module=mod, screen="Follow-up question (chip or input bar)",
         desc="A follow-up: same body plus parent_run_id. The new run joins the same conversation (thread_id).")
    call(c, "GET", f"/runs/{live}", H, template="/runs/{run_id}", module=mod, screen="Research loader (all three modules)",
         desc="Run status for the loader: steps[] with items[] (per-source Searching/Searched), progress_pct, current_activity, "
              "activity (live feed), emerging_findings, elapsed_seconds.",
         notes="Poll about once a second. Stop when status is Completed, Failed, Cancelled or Paused, or stalled is true.")
    call(c, "GET", f"/runs/{ai_done}/result", H, template="/runs/{run_id}/result", module=mod, screen="Answer view",
         desc="Completed answer: answer (Markdown; [n] citations match references[].number), references, top_sources "
              "(View Details) with doi, journal, publication_date, key_findings, research_summary, provider.",
         notes="409 while the run is not Completed. relevant=false: show 'Please ask a relevant question' with message. "
               "follow_ups = suggested next questions from the AI service (show as chips; hide when empty). "
               "A search takes about 10-15 s. If the AI service is not configured or fails, the run is Failed with the reason in step_label.")
    call(c, "GET", f"/runs/{ai_done}/thread", H, template="/runs/{run_id}/thread", module=mod,
         screen="Conversation view (reload / deep link)", desc="Every turn of the conversation, oldest first; completed turns include result.")
    call(c, "GET", f"/runs/{ai_done}/events", H, template="/runs/{run_id}/events", module=mod, screen="Research loader (live stream)",
         desc="Server-Sent Events alternative to polling: one 'data:' line per change; closes when the run finishes or stalls.",
         notes="text/event-stream. Polling GET /runs/{id} is recommended (works in every deployment).")
    call(c, "POST", f"/runs/{live}/notify", H, template="/runs/{run_id}/notify", body={"enabled": True}, module=mod,
         screen="Notify me (bell in loader)", desc="Turns 'Notify me when ready' on or off for a run.")
    call(c, "POST", f"/runs/{live}/pause", H, template="/runs/{run_id}/pause", module=mod, screen="Loader > Pause",
         desc="Pauses an in-progress run between steps.")
    call(c, "POST", f"/runs/{live}/resume", H, template="/runs/{run_id}/resume", module=mod, screen="Resume / Retry (Home, History, loader)",
         desc="Continues a Paused, Failed or stalled run from where it stopped. Returns 202; poll GET /runs/{id}.",
         notes="409 for a run that is still working or already finished.")
    call(c, "POST", f"/runs/{stalled}/cancel", H, template="/runs/{run_id}/cancel", module=mod, screen="Stop button",
         desc="Stops an in-progress or paused run. Show the previous turn of the conversation.")
    call(c, "POST", f"/runs/{ai_done}/rerun", H, template="/runs/{run_id}/rerun", module=mod, screen="Regenerate answer / Rerun",
         desc="Runs the same question again as a new turn in the same conversation. Returns 202.")
    call(c, "POST", f"/runs/{ai_done}/save", H, template="/runs/{run_id}/save", body={"folder": "Scientific Evidence", "title": None},
         module=mod, screen="Save Prompt / Save Search", desc="Saves the question and its result to Workspace (snapshot).",
         notes="Saving the same run again returns already_saved=true. 409 if the run is not Completed.")
    call(c, "POST", "/prompts/rephrase", H, body={"text": "irak4 biomarkers target engagement", "module": "ai"}, module=mod,
         screen="AI Rephrase button (every ask box)", desc="Rewrites the typed question with the AI service; put 'rephrased' back into the input.",
         notes="module: ai | pipeline | cioa. 422 when text is empty. 503 when the AI service is not configured or not answering: "
               "show the message and keep the user's text.")
    call(c, "POST", "/prompts/suggest", H, body={"text": "irak4 in lupus"}, module=mod, screen="(Legacy) AI suggestion",
         desc="Older suggestion endpoint kept for compatibility. Use /prompts/rephrase.")
    call(c, "GET", "/prompts/suggested", H, module=mod, screen="Suggested prompts", desc="Published prompts from the Prompts Library.")

    # ---- Drug Pipeline Explorer
    mod = "Drug Pipeline Explorer"
    call(c, "GET", "/pipeline/options", H, module=mod, screen="Filter dropdowns + More filters dialog",
         desc="Grouped dropdown options (target, indication, modality, stage), option hints and the More filters definition.")
    pipe_done = call(c, "POST", "/runs", H, body={"module": "pipeline", "question": "", "filters": {"indication": "SLE", "stage": "Phase 2"},
                                       "more_filters": {"status": "rec", "phase": ["p2"]}},
         module=mod, screen="Ask box > Submit", desc="Starts a pipeline run. A question or at least one filter is required.",
         notes="filters: target, indication, modality, stage. more_filters: see /pipeline/options.more_filters.").json()["id"]
    call(c, "GET", f"/runs/{pipe_done}/result", H, template="/runs/{run_id}/result", module=mod, screen="Answer: KPI row, five views, AI summary",
         desc="Pipeline answer: kpi_tiles, assets (table), swimlane, stage_counts (graph), heatmap, radar from the database; "
              "summary (Markdown with [n] citations), references and top_sources from the AI service.",
         notes="summary is null for sample runs (they never call the AI service): hide the AI summary panel.")
    call(c, "GET", "/pipeline/assets", H, params={"indication": "SLE", "view": "table", "sort": "stage"}, module=mod,
         screen="Per-answer filter bar / view tabs / column sort",
         desc="Live re-query when a filter, view tab or sort changes (no new run). view: table | swimlane | graph | heatmap | radar.",
         notes="stage also accepts Approved, Regulatory review, On hold, Discontinued. status=rec and phase=p1.. are program-level More filters.")
    call(c, "POST", "/pipeline/assets/query", H, body={"filters": {"indication": "AD"}, "more_filters": {"phase": ["p1", "p2"], "age": ["adult"]},
                                                        "view": "heatmap", "sort": "stage", "desc": False},
         module=mod, screen="More filters dialog > Apply", desc="Same as GET /pipeline/assets with the full More filters object in the body.")

    # ---- Competitive Insights
    mod = "Competitive Insights"
    call(c, "GET", "/cioa/options", H, module=mod, screen="Therapeutic Area / Indication / Molecular Target dropdowns",
         desc="Dropdown options. indications_by_area narrows the Indication list for the chosen area.")
    call(c, "GET", "/cioa/workflows", H, params={"question": "Which endpoints have IRAK4 trials used?"}, module=mod,
         screen="Workflow picker (5 cards, View subcategories)",
         desc="The five workflows with subcategories and report sections. 'recommended' = key to highlight for the question.")
    ci_done = call(c, "POST", "/runs", H, body={"module": "cioa", "question": "Map IRAK4 competitors in SLE", "workflow": "competitive",
                                       "therapeutic_area": "Rheumatology", "indication": "SLE", "molecular_target": "IRAK4 inhibitor"},
         module=mod, screen="Workflow card > Run workflow", desc="Starts a Competitive Insights report with the chosen workflow.",
         notes="workflow: competitive | endpoint | epidemiology | landscape | unmet_need. Missing workflow = recommended one. "
               "The AI service writes the overview and each report section (one search each, run in parallel): about 30-60 s.").json()["id"]
    call(c, "GET", f"/runs/{ci_done}/result", H, template="/runs/{run_id}/result", module=mod, screen="Report card",
         desc="Report: workflow, context, stats (3 KPI tiles, database), key_takeaways and overview (AI), sections[] with text, "
              "bullets and references (AI) and an optional table (database), data_sources.",
         notes="Sample reports in the demo data have no AI text (text null, bullets empty): show the tables only.")
    call(c, "POST", f"/runs/{ci_done}/change-workflow", H, template="/runs/{run_id}/change-workflow", body={"workflow": "unmet_need"},
         module=mod, screen="Change workflow", desc="Runs the same question with another workflow as a new turn; the previous report stays.",
         notes="409 if the report already uses that workflow; 422 for an unknown workflow.")
    call(c, "GET", f"/runs/{ci_done}/report", H, template="/runs/{run_id}/report", module=mod, screen="Export Full Report (PDF)",
         desc="Full report ready to render as PDF: meta table, at_a_glance, key_takeaways, contents, sections, appendices, filename.")
    call(c, "POST", f"/runs/{ci_done}/approve", H, template="/runs/{run_id}/approve", module=mod, screen="Approve report",
         desc="Marks a completed report as approved (edit permission).")
    call(c, "GET", "/cioa/capabilities", H, module=mod, screen="(Deprecated)", desc="Older capability list. Use /cioa/workflows.")

    # ---- Workspace
    mod = "Workspace"
    call(c, "GET", "/workspace/items", H, params={"library": "all", "sort": "updated", "desc": "true"}, module=mod,
         screen="Library tabs, search, Type/Module filters, sort",
         desc="Saved items with tab counts and folder list. library: all | my | shared | archived; also type, module, folder, q.")
    call(c, "GET", f"/workspace/items/{item_id}", H, template="/workspace/items/{item_id}", module=mod, screen="Row click > details drawer",
         desc="Item details: question, saved snapshot (never reruns), versions, shared_with, notes, access (Owner | edit | view).")
    call(c, "PATCH", f"/workspace/items/{item_id}", H, template="/workspace/items/{item_id}", body={"folder": "Scientific Evidence"},
         module=mod, screen="Rename / Move / Archive / Restore", desc="Updates title, folder or archived. Owner only.")
    dup = call(c, "POST", f"/workspace/items/{item_id}/duplicate", H, template="/workspace/items/{item_id}/duplicate", module=mod,
               screen="Duplicate", desc="Copies an item into the user's own workspace.").json()
    call(c, "POST", f"/workspace/items/{item_id}/rerun", H, template="/workspace/items/{item_id}/rerun", module=mod,
         screen="Open > Rerun / Home 'Run again'", desc="Starts a new run from the saved question and settings. Returns 202 with the run.")
    call(c, "POST", f"/workspace/items/{item_id}/versions/{version_id}/restore", H,
         template="/workspace/items/{item_id}/versions/{version_id}/restore", module=mod, screen="Version history > Restore",
         desc="Restores an earlier version as a new version.")
    call(c, "GET", "/users/directory", H, module=mod, screen="Share dialog pickers",
         desc="Colleagues and groups for the share dialog (group_options has ids).")
    call(c, "POST", f"/workspace/items/{item_id}/share", H, template="/workspace/items/{item_id}/share",
         body={"user_id": priya, "permission": "edit"}, module=mod, screen="Share dialog > Share",
         desc="Shares with one colleague (user_id) or one group (group_id or group name). permission: view | edit.",
         notes="422 when both a user and a group are sent, or the group is unknown.")
    share_id = c.get(API + f"/workspace/items/{item_id}", headers=H).json()["shared_with"][-1]["id"]
    call(c, "DELETE", f"/workspace/items/{item_id}/share/{share_id}", H, template="/workspace/items/{item_id}/share/{share_id}",
         module=mod, screen="Share dialog > Remove person", desc="Removes a share.")
    call(c, "POST", f"/workspace/items/{item_id}/notes", H, template="/workspace/items/{item_id}/notes",
         body={"text": "Checked the references, ready for review."}, module=mod, screen="Notes and comments > Post",
         desc="Adds a note. Anyone the item is shared with may comment.")
    call(c, "DELETE", f"/workspace/items/{dup['id']}", H, template="/workspace/items/{item_id}", module=mod,
         screen="Delete (confirm dialog)", desc="Permanently deletes an item and its versions, shares and notes. Owner only.")

    # ---- History
    mod = "History"
    call(c, "GET", "/history/summary", H, module=mod, screen="History KPI tiles",
         desc="activities_this_week, needs_attention, reports_generated, searches_this_week.")
    call(c, "GET", "/history/searches", H, params={"range": "30", "mode": "All"}, module=mod, screen="Search history tab",
         desc="Past AI searches. range: 1 (today) | 7 | 30 | 0 (all time); mode: All | Standard | Deep Research; q = text search.")
    call(c, "GET", "/history/activity", H, params={"range": "7"}, module=mod, screen="Research activity tab (timeline)",
         desc="All runs as a timeline. Filters: range, module, project, kind, q. resumable drives Resume / Retry.")

    # ---- Prioritization Scoring
    mod = "Prioritization Scoring"
    call(c, "GET", "/scoring/models", H, module=mod, screen="Open screen", desc="Published scoring templates.")
    call(c, "GET", "/scoring/assessments", H, module=mod, screen="Open screen", desc="The user's assessments, newest first.")
    a = call(c, "POST", "/scoring/assessments", H, body={"model_id": model_id, "title": "IRAK4 - handover example"}, module=mod,
             screen="Open screen (no assessment yet)", desc="Creates an assessment from a template for the current context.").json()
    aid = a["id"]
    call(c, "GET", f"/scoring/assessments/{aid}", H, template="/scoring/assessments/{aid}", module=mod,
         screen="Criteria, weights, AI scores, results table",
         desc="Full assessment: criteria, weights (+ weight_total, weight_errors), ai_scores, overrides, results with rank and priority.",
         notes="ai_scores values are null (Missing) until the AI service can score: the user enters scores as overrides.")
    keys = [cr["key"] for cr in a["criteria"]]
    call(c, "PUT", f"/scoring/assessments/{aid}/weights", H, template="/scoring/assessments/{aid}/weights",
         body={"weights": dict(zip(keys, [20, 20, 20, 30, 10]))}, module=mod, screen="Weights > Apply & Recalculate",
         desc="Applies weights (percent per criterion). Returns the recalculated assessment.")
    call(c, "PUT", f"/scoring/assessments/{aid}/weights", H, template="/scoring/assessments/{aid}/weights",
         body={"weights": {k: 25 for k in keys}}, module=mod, screen="Weights > Apply (error example)",
         desc="Error example: weights must total 100%.", notes="422 with detail listing the problem.", record=True)
    call(c, "POST", f"/scoring/assessments/{aid}/criteria", H, template="/scoring/assessments/{aid}/criteria",
         body={"name": "Time to market", "rule": "1-5 based on expected launch year", "scale": {}}, module=mod,
         screen="Add Criterion", desc="Adds a criterion at 0% weight; rebalance weights afterwards.")
    new_key = [cr["key"] for cr in c.get(API + f"/scoring/assessments/{aid}", headers=H).json()["criteria"]][-1]
    call(c, "PATCH", f"/scoring/assessments/{aid}/criteria/{new_key}", H, template="/scoring/assessments/{aid}/criteria/{key}",
         body={"name": "Time to market", "rule": "1-5 based on expected US launch year", "scale": {}}, module=mod,
         screen="Edit criterion", desc="Renames a criterion or changes its scoring rule.")
    call(c, "DELETE", f"/scoring/assessments/{aid}/criteria/{new_key}", H, template="/scoring/assessments/{aid}/criteria/{key}",
         module=mod, screen="Remove criterion", desc="Removes a criterion and clears overrides; weight_errors shows if weights need rebalancing.")
    call(c, "PUT", f"/scoring/assessments/{aid}/overrides", H, template="/scoring/assessments/{aid}/overrides",
         body={"indication": "RA", "criterion": "population", "score": 4,
               "justification": "Claims data show a larger biologic-experienced population."},
         module=mod, screen="Review and override AI scores", desc="Overrides an AI score. Sending the AI value again removes the override.",
         notes="score 1-5 or null (Missing). Justification required (422).")
    call(c, "GET", f"/scoring/assessments/{aid}/sensitivity", H, template="/scoring/assessments/{aid}/sensitivity", module=mod,
         screen="Sensitivity analysis accordion", desc="Rank of each indication when one criterion's weight is doubled. delta > 0 = moved up.",
         notes="409 when weights do not total 100%.")
    sc = call(c, "POST", f"/scoring/assessments/{aid}/scenarios", H, template="/scoring/assessments/{aid}/scenarios",
              body={"name": "Payer-weighted"}, module=mod, screen="Save current as scenario", desc="Saves weights and overrides as a named scenario.").json()
    call(c, "GET", f"/scoring/assessments/{aid}/scenarios", H, template="/scoring/assessments/{aid}/scenarios", module=mod,
         screen="Saved scenarios list", desc="Saved scenarios with their ranking.")
    call(c, "POST", f"/scoring/assessments/{aid}/scenarios/{sc['id']}/load", H,
         template="/scoring/assessments/{aid}/scenarios/{sid}/load", module=mod, screen="Scenario > Load",
         desc="Restores a scenario's weights and overrides.")
    call(c, "POST", f"/scoring/assessments/{aid}/reset", H, template="/scoring/assessments/{aid}/reset", module=mod,
         screen="Reset to Default", desc="Restores the template's criteria and weights and clears overrides.")
    call(c, "POST", f"/scoring/assessments/{aid}/save-to-workspace", H, template="/scoring/assessments/{aid}/save-to-workspace",
         module=mod, screen="Save to Workspace", desc="Saves the assessment as a Workspace item.")
    call(c, "DELETE", f"/scoring/assessments/{aid}/scenarios/{sc['id']}", H,
         template="/scoring/assessments/{aid}/scenarios/{sid}", module=mod, screen="Scenario > Delete", desc="Deletes a scenario.")

    # ---- Data Connectors & Admin
    mod = "Data Connectors"
    call(c, "GET", "/datasources", H, params={"category": 0}, module=mod, screen="Data Connectors page (KPIs, tabs, search)",
         desc="Connectors with KPI counts and category names. Filters: category (0-4), q.")
    new_c = call(c, "POST", "/datasources", A, body={"name": "Handover Example Source", "category": 1,
                                                      "description": "Example connector", "license": "Open", "config": {}},
                 module=mod, screen="Add Connector", desc="Adds a connector (starts disabled until tested).").json()
    call(c, "PATCH", f"/datasources/{new_c['id']}", A, template="/datasources/{cid}",
         body={"name": "Handover Example Source", "category": 1, "description": "Updated description", "license": "Licensed", "config": {}},
         module=mod, screen="Edit connector", desc="Edits a connector. Change is audit-logged.")
    call(c, "POST", f"/datasources/{connector}/toggle", A, template="/datasources/{cid}/toggle", params={"enabled": "true"},
         module=mod, screen="Enabled switch", desc="Enables or disables a connector. Disabled sources are never queried.")
    call(c, "POST", f"/datasources/{connector}/test", A, template="/datasources/{cid}/test", module=mod, screen="Test button",
         desc="Tests the connection and updates health.",
         notes="connected=false: no client is connected for this source yet (search goes through the AI service).")

    mod = "Admin"
    call(c, "GET", "/admin/overview", A, module=mod, screen="Admin overview cards", desc="Count and 'N live' badge for each admin section.")
    call(c, "GET", "/admin/users", A, module=mod, screen="Users list", desc="All users with role, groups, status and last login.")
    u = call(c, "POST", "/admin/users", A, body={"name": "Alex Morgan", "email": "alex.morgan@example.com", "role": "Analyst",
                                                  "groups": ["Commercial"], "status": "Active", "password": "ChangeMe123!"},
             module=mod, screen="Add user", desc="Creates a user. role = name from /admin/roles; unknown group names are created.",
             notes="409 duplicate email; 422 unknown role.").json()
    call(c, "PATCH", f"/admin/users/{u['id']}", A, template="/admin/users/{uid}",
         body={"name": "Alex Morgan", "email": "alex.morgan@example.com", "role": "Reviewer", "groups": ["Commercial", "Clinical"],
               "status": "Active"}, module=mod, screen="Edit user", desc="Updates a user. Before and after values are audit-logged.")
    call(c, "GET", "/admin/roles", A, module=mod, screen="Roles & permissions", desc="Roles with their permissions and user counts, plus all permissions.")
    call(c, "PUT", f"/admin/roles/{role_viewer}/permissions", A, template="/admin/roles/{rid}/permissions",
         body={"permissions": ["view"]}, module=mod, screen="Role editor > Save",
         desc="Sets a role's permissions. Takes effect immediately.", notes="409 if you remove 'administer' from your own role.")
    call(c, "GET", "/admin/groups", A, module=mod, screen="Groups list", desc="Groups with member counts.")
    g = call(c, "POST", "/admin/groups", A, body={"name": "Immunology", "description": "Immunology programme team"}, module=mod,
             screen="Add group", desc="Creates a group.", notes="409 duplicate name.").json()
    call(c, "PATCH", f"/admin/groups/{g['id']}", A, template="/admin/groups/{gid}",
         body={"name": "Immunology", "description": "Immunology and inflammation team"}, module=mod, screen="Edit group",
         desc="Renames a group or changes its description.")
    call(c, "DELETE", f"/admin/groups/{g['id']}", A, template="/admin/groups/{gid}", module=mod, screen="Delete group",
         desc="Deletes an empty group.", notes="409 while it has members or items shared with it.")
    call(c, "GET", "/admin/records/prompt", A, template="/admin/records/{kind}", module=mod,
         screen="AI Workflows / Report Templates / Prompts Library", desc="Admin records of one kind: workflow | template | prompt. q = search.")
    rec = call(c, "POST", "/admin/records/prompt", A, template="/admin/records/{kind}",
               body={"name": "Summarise safety signals for {target}", "status": "Draft", "version": "v1",
                     "data": {"scope": "Inflammation › SLE, RA, AD", "variables": "{target}", "visibility": "All users"}},
               module=mod, screen="Add record", desc="Creates a workflow, template or prompt record.").json()
    call(c, "PATCH", f"/admin/records/prompt/{rec['id']}", A, template="/admin/records/{kind}/{rid}",
         body={"name": "Summarise safety signals for {target}", "status": "Published", "version": "v2",
               "data": {"scope": "Inflammation › SLE, RA, AD", "variables": "{target}", "visibility": "All users"}},
         module=mod, screen="Edit record", desc="Updates a record (audit-logged).")
    call(c, "POST", f"/admin/records/prompt/{prompt_id}/deactivate", A, template="/admin/records/{kind}/{rid}/deactivate",
         module=mod, screen="Deactivate record", desc="Sets status to Inactive; history is kept.")
    call(c, "GET", "/admin/scoring-models", A, module=mod, screen="Scoring Models", desc="All scoring templates with criteria.")
    call(c, "POST", "/admin/scoring-models", A, body={"name": "Clinical Feasibility", "version": "v1", "status": "Draft",
                                                       "criteria": [{"key": "endpoints", "name": "Endpoint maturity", "rule": "1-5",
                                                                     "scale": {}, "weight": 100}]},
         module=mod, screen="Add scoring model", desc="Creates a scoring template.", notes="Publishing requires default weights = 100%.")
    call(c, "GET", "/admin/audit", A, params={"limit": 3}, module=mod, screen="Audit log", desc="Who changed what and when, with before/after values.")

    # ---- destructive calls last
    mod = "History"
    call(c, "DELETE", "/history", H, params={"scope": "searches"}, module=mod, screen="Clear history (confirm dialog)",
         desc="Hides history rows (soft delete). Saved searches and Workspace items are not affected.", notes="scope: searches | activity.")
    mod = "Account & platform"
    call(c, "POST", "/auth/logout", H, module=mod, screen="Profile menu > Logout",
         desc="Ends this session: the token stops working (401). Other devices stay signed in.",
         notes="Show the unsaved-edits warning first, then clear the stored token.")
    call(c, "GET", "/health", None, module="Operations", screen="Monitoring", desc="Liveness check. No login needed.", prefix="")
    call(c, "GET", "/health/dependencies", None, module="Operations", screen="Monitoring / Data Connectors",
         desc="Status of external services, e.g. the AI Scientific Search service (configured, ok, latency). No login needed.",
         notes="configured=false: AI runs fail with 'not configured' and AI Rephrase returns 503.", prefix="")

# ------------------------------------------------------------------ coverage check
documented = {(mm.upper(), p) for p, ops in app.openapi()["paths"].items() for mm in ops}
missing = sorted(documented - SEEN)
if missing:
    sys.exit(f"Endpoints without a sample: {missing}")


# ------------------------------------------------------------------ workbook
ARIAL = "Arial"
HEAD_FILL, HEAD_FONT = PatternFill("solid", fgColor="203662"), Font(name=ARIAL, bold=True, color="FFFFFF", size=10)
BODY = Font(name=ARIAL, size=10)
MONO = Font(name="Courier New", size=9)
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP_TOP = Alignment(wrap_text=True, vertical="top")
METHOD_COLOR = {"GET": "15803D", "POST": "1D4ED8", "PUT": "B45309", "PATCH": "7C3AED", "DELETE": "B91C1C"}

wb = Workbook()

# Read Me
ws = wb.active
ws.title = "Read Me"
readme = [
    ("API Handover - New Indication Intelligence Platform", None),
    ("Backend v0.4 · for UI integration", None),
    ("", None),
    ("Base URL", "http://localhost:8000/api/v1   (interactive docs: http://localhost:8000/docs)"),
    ("Sign in", "POST /auth/login with {email, password}. Send 'Authorization: Bearer <access_token>' on every other call."),
    ("Demo logins", "Password ChangeMe123! · swanik@example.com (Analyst) · maria.chen@example.com (Admin) · "
                    "sam.patel@example.com (Reviewer) · priya.nair@example.com (Analyst)"),
    ("Permissions", "Each row lists the permission needed. Roles: Admin = all; Analyst = view, create, edit, execute, share; "
                    "Reviewer = view, edit, share; Viewer = view. GET /me returns the user's permissions."),
    ("Errors", "Body is {\"detail\": \"message\"}. 401 = sign in again · 403 = missing permission (hide the action) · "
               "404 = not found or no access · 409 = wrong state (e.g. export before completion) · 422 = validation (show detail)."),
    ("Research runs", "AI Search, Pipeline and Competitive Insights share one flow: POST /runs returns 202 -> poll GET /runs/{id} "
                      "(about once a second) -> when status is Completed, GET /runs/{id}/result. Follow-ups: send parent_run_id; "
                      "GET /runs/{id}/thread rebuilds the conversation."),
    ("Run status -> UI", "In Progress = loader (if stalled=true: stop polling, show Resume) · Paused / Failed = Resume / Retry · "
                         "Completed = result · Cancelled = show the previous turn."),
    ("Dates", "ISO-8601 in UTC with an offset, e.g. 2026-11-24T09:15:00+00:00. Format in the browser."),
    ("AI content", "AI answers, pipeline summaries and report text come from the AI Scientific Search service; nothing is "
                   "made up when it is unavailable (the run fails with the reason). Sample runs in the demo data have no AI "
                   "text. AI scores in Scoring start as Missing (null) until the user enters a score."),
    ("Samples in this sheet", "Real responses from the demo data; AI answers are a recorded reply from the AI service. "
                              "Long lists show 2 items then '... N more'; long text is truncated."),
    ("Sheets", "APIs = every endpoint (filter the Module column to see one screen) with sample request and response · Values = allowed values for status, module, workflow and other fields."),
]
for i, (k, v) in enumerate(readme, 1):
    ws.cell(i, 1, k).font = Font(name=ARIAL, bold=True, size=16 if i == 1 else 10, color="203662" if i == 1 else "000000")
    if v:
        ws.cell(i, 2, v).font = BODY
        ws.cell(i, 2).alignment = WRAP_TOP
        ws.cell(i, 1).alignment = WRAP_TOP
ws["A2"].font = Font(name=ARIAL, size=10, color="6E7076")
ws.column_dimensions["A"].width = 24
ws.column_dimensions["B"].width = 120

# APIs
ws = wb.create_sheet("APIs")
cols = [("#", 5), ("Module", 20), ("Screen / UI action", 30), ("Method", 9), ("Endpoint", 40), ("Permission", 12),
        ("Description", 48), ("Sample request", 55), ("Sample response", 80), ("Status", 8), ("Notes", 40)]
for i, (h, w) in enumerate(cols, 1):
    cell = ws.cell(1, i, h)
    cell.font, cell.fill, cell.border = HEAD_FONT, HEAD_FILL, BORDER
    cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.column_dimensions[get_column_letter(i)].width = w
for n, r in enumerate(ROWS, 1):
    vals = [n, r["module"], r["screen"], r["method"], r["endpoint"], r["perm"], r["desc"], r["request"], r["response"],
            r["status"], r["notes"]]
    for i, v in enumerate(vals, 1):
        cell = ws.cell(n + 1, i, v)
        cell.font = MONO if i in (5, 8, 9) else BODY
        cell.alignment, cell.border = WRAP_TOP, BORDER
    ws.cell(n + 1, 4).font = Font(name=ARIAL, size=10, bold=True, color=METHOD_COLOR.get(r["method"], "000000"))
    lines = max(str(r["response"]).count("\n"), str(r["request"]).count("\n")) + 1
    ws.row_dimensions[n + 1].height = min(400, max(30, 11.5 * min(lines, 34)))
ws.freeze_panes = "E2"
ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{len(ROWS) + 1}"

# Values
ws = wb.create_sheet("Values")
values = [
    ("Run status", "In Progress, Paused, Failed, Completed, Cancelled"),
    ("Run module", "ai (AI Scientific Search), pipeline (Drug Pipeline Explorer), cioa (Competitive Insights)"),
    ("Run kind", "Search, Workflow run, Report generated"),
    ("Research mode", "Standard, Deep Research"),
    ("Step / item status", "pending, active, done, failed, skipped"),
    ("Competitive Insights workflow", ", ".join(f"{w['key']} ({w['name']})" for w in catalog.WORKFLOWS)),
    ("Pipeline stage", ", ".join(pl.STAGES) + "; filter also accepts Approved, Regulatory review, On hold, Discontinued"),
    ("Pipeline view", "table, swimlane, graph, heatmap, radar"),
    ("Home tab", "in_progress, recent_results, saved"),
    ("Home row action", "resume, view, run_again"),
    ("Workspace library", "all, my, shared, archived"),
    ("Workspace item type", "Search, Question set, Report, Assessment, Pipeline view"),
    ("Workspace access", "Owner, edit, view"),
    ("Share permission", "view, edit"),
    ("History range", "1 (today), 7, 30, 0 (all time)"),
    ("Priority band (scoring)", "High (80+), Medium (60-79), Low (below 60)"),
    ("Role", ", ".join(catalog.ROLES)),
    ("Permission", ", ".join(catalog.PERMISSIONS)),
    ("Connector category", ", ".join(f"{i} = {n}" for i, n in enumerate(__import__('app.services.integrations', fromlist=['CATEGORIES']).CATEGORIES))),
    ("Connector status", "Healthy, Warning, Failed, Disabled"),
    ("Notification route", "ai, pipeline, cioa, workspace, home (screen to open)"),
]
for i, h in enumerate(["Field", "Allowed values"], 1):
    cell = ws.cell(1, i, h)
    cell.font, cell.fill, cell.border = HEAD_FONT, HEAD_FILL, BORDER
for n, (k, v) in enumerate(values, 2):
    for i, val in enumerate((k, v), 1):
        cell = ws.cell(n, i, val)
        cell.font, cell.alignment, cell.border = (Font(name=ARIAL, bold=True, size=10) if i == 1 else BODY), WRAP_TOP, BORDER
ws.column_dimensions["A"].width = 30
ws.column_dimensions["B"].width = 130
ws.freeze_panes = "A2"

wb.save(OUT)
print(f"ok {OUT} · {len(ROWS)} rows · {len(SEEN)} endpoints covered")
