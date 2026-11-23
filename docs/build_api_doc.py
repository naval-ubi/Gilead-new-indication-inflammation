"""Builds docs/API_Screen_Integration_Guide.pdf - endpoint <-> screen map for FE integration.

Usage (needs `pip install reportlab`, not an app dependency):
    python docs/build_api_doc.py docs/API_Screen_Integration_Guide.pdf
Update the rows below whenever an endpoint is added or changed."""
import sys
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

OUT = sys.argv[1]
RED = colors.HexColor("#c5203f")
NAVY = colors.HexColor("#203662")
INK = colors.HexColor("#1d1e21")
MUT = colors.HexColor("#6e7076")
BD = colors.HexColor("#e4e5e7")
BG = colors.HexColor("#f7f7f8")
TINT = colors.HexColor("#fbedf0")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=22, leading=27, textColor=INK, alignment=TA_LEFT, spaceAfter=4)
SUB = ParagraphStyle("SUB", parent=ss["Normal"], fontName="Helvetica", fontSize=10.5, leading=15, textColor=MUT, spaceAfter=10)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=NAVY, spaceBefore=10, spaceAfter=4, keepWithNext=1)
H3 = ParagraphStyle("H3", parent=ss["Heading3"], fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=INK, spaceBefore=6, spaceAfter=3)
BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontName="Helvetica", fontSize=9.2, leading=13, textColor=INK, spaceAfter=4)
INTRO = ParagraphStyle("INTRO", parent=BODY, keepWithNext=1)
NOTE = ParagraphStyle("NOTE", parent=BODY, fontSize=8.6, leading=12, textColor=MUT)
CELL = ParagraphStyle("CELL", parent=BODY, fontSize=8.3, leading=11, spaceAfter=0)
CELLB = ParagraphStyle("CELLB", parent=CELL, fontName="Helvetica-Bold")
HEAD = ParagraphStyle("HEAD", parent=CELL, fontName="Helvetica-Bold", textColor=INK)
CODE = ParagraphStyle("CODE", parent=CELL, fontName="Courier", fontSize=7.9, leading=10.5)


def ep(method, path):
    col = {"GET": "#15803d", "POST": "#1d4ed8", "PUT": "#b45309", "PATCH": "#7c3aed", "DELETE": "#b91c1c"}[method]
    return f'<font color="{col}"><b>{method}</b></font> <font name="Courier">{path}</font>'


def table(rows, widths=(42 * mm, 74 * mm, 58 * mm), head=("Screen element / action", "Endpoint", "Notes for FE")):
    data = [[Paragraph(h, HEAD) for h in head]]
    for r in rows:
        data.append([Paragraph(r[0], CELLB), Paragraph("<br/>".join(ep(*e) for e in r[1]), CODE), Paragraph(r[2], CELL)])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BG), ("LINEBELOW", (0, 0), (-1, 0), 0.8, BD),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BD), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("BOX", (0, 0), (-1, -1), 0.6, BD),
    ]))
    return t


def kv(rows, widths=(40 * mm, 134 * mm)):
    t = Table([[Paragraph(a, CELLB), Paragraph(b, CELL)] for a, b in rows], colWidths=widths)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, BD),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                           ("LEFTPADDING", (0, 0), (-1, -1), 4), ("BOX", (0, 0), (-1, -1), 0.6, BD)]))
    return t


def section(title, intro, rows, extra=None):
    out = [Paragraph(title, H2)]
    if intro:
        out.append(Paragraph(intro, INTRO))
    if extra:
        out.append(Paragraph(extra, NOTE))
    out.append(table(rows))
    return out


def callout(text):
    t = Table([[Paragraph(text, CELL)]], colWidths=[174 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT), ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#eebcc5")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


story = [
    Paragraph("API-to-Screen Integration Guide", H1),
    Paragraph(f"New Indication Intelligence Platform · Backend v0.4 · {date.today():%d %b %Y}<br/>"
              "Which endpoint each screen and control calls, for wiring the updated UI design to the FastAPI backend.", SUB),
    Paragraph("1. Integration basics", H2),
    kv([
        ("Base URL", "<font name='Courier'>/api/v1</font> (interactive schema and try-it-out at <font name='Courier'>/docs</font>)"),
        ("Auth", "<font name='Courier'>POST /auth/login {email, password}</font> &#8594; <font name='Courier'>access_token</font>. "
                 "Send <font name='Courier'>Authorization: Bearer &lt;token&gt;</font> on every call. 401 (expired or logged-out token) = clear the token and show sign-in. "
                 "<font name='Courier'>POST /auth/logout</font> ends the session server-side."),
        ("Errors", "<b>422</b> validation (show <font name='Courier'>detail</font> inline, e.g. weights is not 100%, missing justification) · "
                   "<b>403</b> missing permission (hide/disable the control) · <b>404</b> not found or no access · "
                   "<b>409</b> wrong state (e.g. export before a run completes)."),
        ("Permissions", "Role &#8594; permissions: Admin (all), Analyst (view, create, edit, execute, share), Reviewer (view, edit, share), "
                        "Viewer (view). <font name='Courier'>GET /me</font> returns the role; use it to show/hide actions."),
        ("Dates", "ISO-8601 UTC. Format in the browser (e.g. “Nov 13, 2026”)."),
    ]),
    Paragraph("2. The research run pattern (AI Search, Pipeline, Competitive Insights)", H2),
    Paragraph("All three research screens use one job API. Learn it once:", BODY),
    kv([
        ("1 · Start", f"{ep('POST', '/runs')} &#8594; <b>202</b> with the run (id, steps, thread_id). "
                      "Body: <font name='Courier'>module</font> = <font name='Courier'>ai | pipeline | cioa</font>, plus the module fields below."),
        ("2 · Track", f"Poll {ep('GET', '/runs/{id}')} every ~1 s, or open {ep('GET', '/runs/{id}/events')} (SSE; closes on a terminal status). "
                      "Render the loader from <font name='Courier'>steps[].items[]</font>, <font name='Courier'>progress_pct</font>, "
                      "<font name='Courier'>current_activity</font>, <font name='Courier'>activity</font>, "
                      "<font name='Courier'>emerging_findings</font>, <font name='Courier'>elapsed_seconds</font>."),
        ("3 · Result", f"When <font name='Courier'>status = Completed</font>: {ep('GET', '/runs/{id}/result')}. "
                       "If <font name='Courier'>result.relevant</font> is false, show the “Please ask a relevant question” card with "
                       "<font name='Courier'>message</font> and <font name='Courier'>follow_ups</font>."),
        ("4 · Follow-up", "Send the previous run id as <font name='Courier'>parent_run_id</font>. Rebuild the conversation from "
                          f"{ep('GET', '/runs/{id}/thread')} (turns oldest first)."),
        ("Stop / resume", f"{ep('POST', '/runs/{id}/cancel')} (stop button) · /pause · /resume (History) · /rerun (Regenerate)."),
        ("Notify me", f"{ep('POST', '/runs/{id}/notify')} <font name='Courier'>{{enabled}}</font> or <font name='Courier'>notify: true</font> "
                      "on create. A bell notification is created on completion even if in-app notifications are off."),
    ]),
    Spacer(1, 4),
    callout("<b>Status &#8594; UI.</b> In Progress = loader, unless <font name='Courier'>stalled</font> is true (no progress for 15 min, "
            "e.g. after a server restart): stop polling and show Resume · Paused/Failed = Resume button (History, Home) · Completed = result · "
            "Cancelled = show the previous turn. Step status: pending / active / done / failed / skipped (skipped = out-of-scope question). "
            "Source items in the search stage map to “Queued / Searching… / Searched”."),
    PageBreak(),
]

story += section("3. Global shell (header, navigation, help)", None, [
    ("Sign in", [("POST", "/auth/login")], "Store token; response includes user, role, permissions, context, prefs."),
    ("App boot / header avatar", [("GET", "/me")], "Name, role, permissions (show/hide actions), context, prefs."),
    ("Profile menu → My Profile", [("GET", "/me/profile"), ("PATCH", "/me/profile")],
     "initials, name, role, groups, permissions, active_session, last_login. PATCH {name} edits the display name only."),
    ("Profile menu → Logout", [("POST", "/auth/logout")],
     "204. Revokes this token only (other devices stay signed in). Show the unsaved-edits warning first, "
     "then clear the stored token and show the signed-out screen. Saved work is kept."),
    ("Research context dialog", [("GET", "/context/options"), ("PUT", "/me/context")],
     "Options: grouped indications, geographies, therapeutic areas, molecular targets, module display names. "
     "Context is used as the default for every new run."),
    ("Preferences dialog", [("PUT", "/me/preferences")], "{deep_research, in_app_notifications, email}."),
    ("Notification bell + badge", [("GET", "/notifications"), ("POST", "/notifications/{nid}/read"), ("POST", "/notifications/read-all")],
     "<font name='Courier'>unread</font> drives the badge. Each item has <font name='Courier'>route</font> (ai, pipeline, cioa, workspace…) to navigate to."),
    ("Help menu (Docs, Guides, FAQs)", [("GET", "/help/content")], "docs[], guides[] (numbered steps), faq[], chat_suggestions[]."),
    ("AI Help Chat", [("POST", "/help/chat")], "{message} &#8594; {reply}."),
])

story += section("4. Home", "Hero ask box, three KPI cards, and the card list for the selected KPI.", [
    ("KPI cards (In Progress / Completed / Saved Searches)", [("GET", "/home/summary")],
     "<font name='Courier'>cards[]</font>: key, label, badge, date_label, action, count. "
     "<font name='Courier'>preview[]</font> = items shown before a card is opened."),
    ("Card list after clicking a KPI", [("GET", "/home?tab=in_progress|recent_results|saved")],
     "Rows: title, module_name, last_worked, badge, date_label, <font name='Courier'>progress_pct</font> (in progress only), action, "
     "stalled, resumable (runs) or can_run_again (saved items)."),
    ("Card arrow: Resume / View result", [("GET", "/runs/{id}/thread")], "Open the module screen with the run's thread (kind = run)."),
    ("Card arrow: Run again (Saved)", [("POST", "/workspace/items/{id}/rerun")], "202 with a new run &#8594; open AI/Pipeline/CI loader."),
    ("Card more-actions menu: Duplicate / Delete", [("POST", "/home/runs/{id}")], "{action: duplicate | delete} for runs; for saved items use the Workspace endpoints."),
    ("Ask box submit", [("POST", "/runs")], "module = ai; then navigate to AI Scientific Search."),
    ("AI Rephrase button", [("POST", "/prompts/rephrase")], "{text, module} &#8594; {rephrased}. Replace the textarea value; user submits."),
])

story += section("5. AI Scientific Search", "Conversational research with a staged loader, references and follow-ups.", [
    ("Landing ask box + Submit", [("POST", "/runs")], "{module: ai, question, deep_research?, sources?, notify?}. Without sources the module defaults are used."),
    ("AI Rephrase", [("POST", "/prompts/rephrase")], "module = ai."),
    ("Research loader (stages, Show details, Live activity, Emerging findings)", [("GET", "/runs/{id}"), ("GET", "/runs/{id}/events")],
     "Stages = steps[]; per-stage detail = steps[i].items[]; Live activity = activity[]; findings carry evidence.label "
     "(Clinical / Preclinical / Genetic / Mechanistic) and preliminary = true."),
    ("Notify me (bell in loader / input bar)", [("POST", "/runs/{id}/notify")], "Toggle; reflect run.notify."),
    ("Stop button", [("POST", "/runs/{id}/cancel")], "Then show the last completed turn."),
    ("Answer", [("GET", "/runs/{id}/result")],
     "result.answer (markdown), references[], follow_ups[], research_summary (stages, sources_searched, elapsed_seconds) for the "
     "“Research complete” strip."),
    ("View Details drawer", [("GET", "/runs/{id}/result")], "key_findings[] + top_sources[{name, url}]."),
    ("Follow-up question chips / input bar", [("POST", "/runs")], "Same body + parent_run_id = last turn's id."),
    ("Conversation history (reload/deep link)", [("GET", "/runs/{id}/thread")], "Render each turn; completed turns include result."),
    ("Regenerate answer", [("POST", "/runs/{id}/rerun")], "New run in the same thread."),
    ("Save Prompt / Save conversation", [("POST", "/runs/{id}/save")], "{folder?, title?} &#8594; item_id. 409 if the run is not completed."),
    ("Share", [("GET", "/users/directory"), ("POST", "/workspace/items/{id}/share")], "Save first, then share the workspace item."),
    ("Data sources dialog", [("GET", "/datasources")], "Show enabled connectors; pass chosen names as sources on POST /runs."),
])

story += section("6. Drug Pipeline Explorer", "Conversation of pipeline answers; each answer has its own filter bar, five view tabs and an AI summary.", [
    ("Filter dropdowns (Target, Indication, Modality, Development Stage)", [("GET", "/pipeline/options")],
     "Use <font name='Courier'>groups</font> for grouped lists, <font name='Courier'>hints</font> for sub-labels, "
     "<font name='Courier'>more_filters</font> to build the More filters dialog."),
    ("Ask box submit", [("POST", "/runs")],
     "{module: pipeline, question?, filters{target, indication, modality, stage}, more_filters?}. A question OR a filter is required."),
    ("Loader", [("GET", "/runs/{id}")], "Same loader contract as AI Search."),
    ("Answer: KPI row + views + AI summary", [("GET", "/runs/{id}/result")],
     "kpi_tiles[], assets (table), swimlane, stage_counts (graph), heatmap, radar, summary, follow_ups, more_filters_count."),
    ("Per-answer filter bar / view tabs / column sort", [("GET", "/pipeline/assets"), ("POST", "/pipeline/assets/query")],
     "Live re-query, no new run. Params: filters + view=table|swimlane|graph|heatmap|radar + sort/desc. "
     "Use the POST form when More filters are set."),
    ("More filters dialog (Apply)", [("POST", "/pipeline/assets/query")], "Badge count = more_filters_count."),
    ("Follow-up / Edit Prompt / Regenerate", [("POST", "/runs"), ("POST", "/runs/{id}/rerun")], "parent_run_id for follow-ups."),
    ("Save Search / Share", [("POST", "/runs/{id}/save"), ("POST", "/workspace/items/{id}/share")], ""),
], extra="Stage filter accepts Discovery…Marketed plus Approved (= Marketed), Regulatory review (= Filed), On hold, Discontinued.")

story += section("7. Competitive Insights", "Ask &#8594; choose one of five workflows &#8594; report. API module key remains <font name='Courier'>cioa</font>.", [
    ("Therapeutic Area / Indication / Molecular Target dropdowns", [("GET", "/cioa/options")],
     "indications_by_area narrows the Indication list; molecular_targets has Direct IRAK4 / Related pathway groups."),
    ("Workflow picker (5 cards, View subcategories)", [("GET", "/cioa/workflows?question=…")],
     "Each workflow: key, name, description, icon, subcategories[], sections[], sources[]. <font name='Courier'>recommended</font> = key to highlight."),
    ("Run workflow", [("POST", "/runs")],
     "{module: cioa, question, workflow, therapeutic_area?, indication?, molecular_target?, notify?}. Missing workflow &#8594; recommended one."),
    ("Loader", [("GET", "/runs/{id}")], "Stage 3 items = the workflow's subcategories."),
    ("Report card", [("GET", "/runs/{id}/result")],
     "workflow, context, stats[3] (value, label, icon), key_takeaways[], sections[{title, bullets[], table?}], data_sources[]."),
    ("Change workflow", [("POST", "/runs/{id}/change-workflow")], "{workflow} &#8594; new run in the same thread; previous report stays."),
    ("Export Full Report (PDF)", [("GET", "/runs/{id}/report")],
     "Render-ready JSON: meta table, at_a_glance, key_takeaways, contents, sections, appendices, footer, filename. FE renders the PDF."),
    ("Save Search / Share / Approve", [("POST", "/runs/{id}/save"), ("POST", "/runs/{id}/approve")], "Approve requires the edit permission."),
])

story += section("8. Prioritization Scoring", "One assessment per user context; all math is server-side. <font name='Courier'>{A}</font> = <font name='Courier'>/scoring/assessments/{aid}</font>.", [
    ("Open screen", [("GET", "/scoring/models"), ("GET", "/scoring/assessments"), ("POST", "/scoring/assessments")],
     "Reuse the latest assessment, else create one {model_id}. Response = full view (criteria, weights, results, ai_scores, overrides)."),
    ("Weight dropdowns &#8594; Apply &amp; Recalculate", [("PUT", "{A}/weights")],
     "{weights: {key: %}}. 422 unless total = 100. Keep unapplied edits client-side (“Unapplied changes”)."),
    ("Add / Edit / Remove criterion", [("POST", "{A}/criteria"), ("PATCH", "{A}/criteria/{key}"),
                                       ("DELETE", "{A}/criteria/{key}")], "{name, rule, scale}. New criteria start at 0%."),
    ("Reset to Default", [("POST", "{A}/reset")], ""),
    ("Override AI score", [("PUT", "{A}/overrides")],
     "{indication, criterion, score (1-5 or null = Missing), justification}. Justification required (422)."),
    ("Sensitivity analysis accordion", [("GET", "{A}/sensitivity")], "Ranks with each weight doubled; delta &gt; 0 = moved up."),
    ("Saved scenarios", [("POST", "{A}/scenarios"), ("GET", "{A}/scenarios"),
                         ("POST", "{A}/scenarios/{sid}/load"), ("DELETE", "{A}/scenarios/{sid}")], "{name}."),
    ("Save to Workspace", [("POST", "{A}/save-to-workspace")], ""),
])

story += section("9. Workspace", None, [
    ("Library tabs + counts, search, Type/Module filters, sort", [("GET", "/workspace/items")],
     "?library=all|my|shared|archived&amp;type=&amp;module=&amp;q=&amp;sort=updated|title&amp;desc= &#8594; counts, items, folders."),
    ("Row click &#8594; details drawer", [("GET", "/workspace/items/{id}")], "Question, saved snapshot (never reruns), versions, shared_with, notes, access."),
    ("Open / Rerun", [("POST", "/workspace/items/{id}/rerun")], "Open = render snapshot; Rerun starts a new run."),
    ("Rename / Archive / Restore", [("PATCH", "/workspace/items/{id}")], "{title?, folder?, archived?}. Owner only."),
    ("Duplicate / Delete", [("POST", "/workspace/items/{id}/duplicate"), ("DELETE", "/workspace/items/{id}")], ""),
    ("Share dialog / permission change", [("GET", "/users/directory"), ("POST", "/workspace/items/{id}/share"),
                                          ("DELETE", "/workspace/items/{id}/share/{share_id}")],
     "Directory gives users[] and group_options[{id, name}]. Body: {user_id} OR {group_id} (or group name), plus permission view | edit. "
     "Sending both a user and a group, or an unknown group, returns 422."),
    ("Restore version", [("POST", "/workspace/items/{id}/versions/{version_id}/restore")], ""),
    ("Notes and comments", [("POST", "/workspace/items/{id}/notes")], "{text}; view-only collaborators may comment."),
])

story += section("10. History", None, [
    ("KPI tiles", [("GET", "/history/summary")], "activities_this_week, needs_attention, reports_generated, searches_this_week."),
    ("Search history tab", [("GET", "/history/searches")], "?q=&amp;range=1|7|30|0&amp;mode=Standard|Deep Research. Reopen = thread; Rerun = /runs/{id}/rerun."),
    ("Research activity tab (timeline)", [("GET", "/history/activity")], "?range=&amp;module=&amp;project=&amp;kind=&amp;q=. Group by day client-side; resumable flag drives Resume/Retry."),
    ("Resume / Retry", [("POST", "/runs/{id}/resume")], "Paused or Failed runs only (409 otherwise)."),
    ("Clear history", [("DELETE", "/history?scope=searches|activity")], "Soft delete; saved items untouched."),
])

story += section("11. Data Connectors &amp; Admin", "Admin endpoints require the <i>administer</i> permission.", [
    ("Data Connectors page (KPIs, category tabs, search)", [("GET", "/datasources")], "?category=&amp;q= &#8594; categories, kpis, items."),
    ("Add / Edit connector", [("POST", "/datasources"), ("PATCH", "/datasources/{cid}")], "{name, category, description, license}. New = disabled."),
    ("Enable switch / Test", [("POST", "/datasources/{cid}/toggle"), ("POST", "/datasources/{cid}/test")], "toggle takes ?enabled=true|false."),
    ("Admin overview cards", [("GET", "/admin/overview")], "sections[]: key, name, count, badge (“3 live”, “39 enabled”)."),
    ("Users", [("GET", "/admin/users"), ("POST", "/admin/users"), ("PATCH", "/admin/users/{uid}")],
     "{name, email, role (name from /admin/roles), groups [names], status, password?}. Unknown role = 422; new group names are created. "
     "Responses include role_id and group_ids."),
    ("Roles &amp; permissions", [("GET", "/admin/roles"), ("PUT", "/admin/roles/{rid}/permissions")],
     "roles[{id, name, description, user_count, permissions[]}] + permissions[{code, description}]. PUT {permissions: [codes]} "
     "takes effect immediately; you cannot remove administer from your own role (409)."),
    ("Groups", [("GET", "/admin/groups"), ("POST", "/admin/groups"), ("PATCH", "/admin/groups/{gid}"), ("DELETE", "/admin/groups/{gid}")],
     "{name, description}; list includes member_count. Duplicate name = 409. Delete only when the group has no members and no shares (409)."),
    ("AI Workflows / Report Templates / Prompts Library", [("GET", "/admin/records/{kind}"), ("POST", "/admin/records/{kind}"),
                                                          ("PATCH", "/admin/records/{kind}/{rid}"), ("POST", "/admin/records/{kind}/{rid}/deactivate")],
     "kind = workflow | template | prompt. Prompt data: scope, variables, visibility."),
    ("Scoring Models", [("GET", "/admin/scoring-models"), ("POST", "/admin/scoring-models")], "Publishing requires default weights = 100%."),
    ("Audit log", [("GET", "/admin/audit")], ""),
])

story += [Spacer(1, 6), Paragraph("12. Stubbed today", H2), Paragraph(
    "Response shapes are final, but answer text, report section text, AI Rephrase, the out-of-scope check, follow-up suggestions "
    "and connector records are placeholders until real LLM and data-source clients are wired in. Build the UI against the shapes, "
    "not the wording.", BODY)]


def deco(c, d):
    c.saveState()
    c.setFillColor(RED)
    c.rect(0, A4[1] - 5, A4[0], 5, fill=1, stroke=0)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(MUT)
    c.drawString(18 * mm, 10 * mm, "API-to-Screen Integration Guide · Backend v0.4")
    c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {d.page}")
    c.restoreState()


doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                        title="API Screen Integration Guide", author="Backend team")
doc.build(story, onFirstPage=deco, onLaterPages=deco)
print("ok", OUT)
