"""Builds docs/Database_ER_Guide.pdf from the live SQLAlchemy models (app/db/models.py).

Column tables, keys, indexes and the PostgreSQL DDL appendix are read from the metadata, so they cannot drift from
the code. Descriptions, the diagram layout and the command reference are maintained in this file.

Usage (from the repo root; needs reportlab, sqlalchemy and pydantic-settings):
    PYTHONPATH=. python docs/build_er_doc.py docs/Database_ER_Guide.pdf
"""
import sys
from datetime import date

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (CondPageBreak, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table,
                                TableStyle)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

import app.db.models  # noqa: F401  (registers every table on Base.metadata)
from app.db.base import Base

OUT = sys.argv[1] if len(sys.argv) > 1 else "docs/Database_ER_Guide.pdf"
T = {t.name: t for t in Base.metadata.sorted_tables}

RED, NAVY, INK, MUT = (colors.HexColor(c) for c in ("#c5203f", "#203662", "#1d1e21", "#6e7076"))
BD, BG, TINT = (colors.HexColor(c) for c in ("#e4e5e7", "#f7f7f8", "#fbedf0"))
DOMAIN_COL = {"Identity & platform": colors.HexColor("#203662"), "Research runs": colors.HexColor("#c5203f"),
              "Workspace": colors.HexColor("#0f766e"), "Prioritization scoring": colors.HexColor("#b45309"),
              "Reference & configuration": colors.HexColor("#6e7076")}

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=22, leading=27, textColor=INK, alignment=TA_LEFT)
SUB = ParagraphStyle("SUB", parent=ss["Normal"], fontSize=10.5, leading=15, textColor=MUT, spaceAfter=10)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=NAVY,
                    spaceBefore=10, spaceAfter=4, keepWithNext=1)
H3 = ParagraphStyle("H3", parent=ss["Heading3"], fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=INK,
                    spaceBefore=8, spaceAfter=2, keepWithNext=1)
BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontSize=9.2, leading=13, textColor=INK, spaceAfter=4)
INTRO = ParagraphStyle("INTRO", parent=BODY, keepWithNext=1, textColor=MUT)
CELL = ParagraphStyle("CELL", parent=BODY, fontSize=8, leading=10.5, spaceAfter=0)
CELLB = ParagraphStyle("CELLB", parent=CELL, fontName="Helvetica-Bold")
CMONO = ParagraphStyle("CMONO", parent=CELL, fontName="Courier", fontSize=7.8)
HEAD = ParagraphStyle("HEAD", parent=CELL, fontName="Helvetica-Bold")
CODE = ParagraphStyle("CODE", fontName="Courier", fontSize=7.6, leading=9.6, textColor=INK, backColor=BG,
                      borderPadding=(5, 6, 5, 6), leftIndent=6, rightIndent=6, spaceBefore=4, spaceAfter=8)
DDL = ParagraphStyle("DDL", parent=CODE, fontSize=6.9, leading=8.4)


def mono(s):
    return f"<font name='Courier'>{s}</font>"


# ------------------------------------------------------------------------------------------- descriptions
TABLES = [  # name, domain, purpose
    ("users", "Identity & platform", "People who sign in. Holds role (permissions), groups (for sharing), the header research context and preferences."),
    ("revoked_tokens", "Identity & platform", "Login tokens ended by POST /auth/logout. Checked on every authenticated request."),
    ("notifications", "Identity & platform", "Bell notifications: research completed/failed, item shared with you."),
    ("audit_log", "Identity & platform", "Who changed what and when (admin edits, overrides, approvals, deletes, logouts) with before/after JSON."),
    ("runs", "Research runs", "One execution of AI Scientific Search, Drug Pipeline Explorer or Competitive Insights. Steps, progress, result, conversation thread."),
    ("workspace_items", "Workspace", "Saved research (search, report, pipeline view, assessment). Holds a frozen snapshot; reopening never reruns."),
    ("item_versions", "Workspace", "Version history of a workspace item (v1, v2 ...) with the snapshot of each version, for Restore."),
    ("item_shares", "Workspace", "Who an item is shared with: a user or a group, with view or edit permission."),
    ("item_notes", "Workspace", "Notes and comments on a workspace item."),
    ("scoring_models", "Prioritization scoring", "Admin-managed scoring templates: criteria, 1-5 scale definitions, default weights."),
    ("assessments", "Prioritization scoring", "A user's working copy of a scoring model for a target and indications: weights, AI scores, overrides."),
    ("scenarios", "Prioritization scoring", "Named snapshots of an assessment's weights and overrides with the resulting ranking."),
    ("pipeline_assets", "Reference & configuration", "Drug programs behind the Pipeline Explorer and Competitive Insights statistics."),
    ("connectors", "Reference & configuration", "Data source registry (PubMed, Citeline ...): enabled switch, health, licence. Secrets live in a vault."),
    ("admin_records", "Reference & configuration", "Generic admin-managed rows: AI workflows, report templates, prompts library."),
]
DOMAIN = {n: d for n, d, _ in TABLES}

COL = {
    "users": {"id": "Primary key.", "name": "Display name (editable via PATCH /me/profile).", "email": "Login; unique, stored lower-case.",
              "password_hash": "scrypt hash (salt$hash). Empty when SSO is used.", "role": "Admin | Analyst | Reviewer | Viewer. Maps to a permission set.",
              "groups": "Group names used for group sharing, e.g. [\"Commercial\", \"Clinical\"].", "status": "Active | Inactive. Inactive users cannot sign in.",
              "last_login": "Set on each successful login.", "context": "Header research context; default for every new run.",
              "prefs": "Preferences dialog values."},
    "revoked_tokens": {"jti": "Token id from the JWT jti claim.", "user_id": "Owner of the token.",
                       "expires_at": "Token's own expiry; rows after this time can be purged.", "revoked_at": "When the user logged out."},
    "notifications": {"user_id": "Recipient.", "title": "Bold first line.", "body": "Detail line.",
                      "route": "Screen to open on click: ai | pipeline | cioa | workspace | home ...", "unread": "Drives the bell badge."},
    "audit_log": {"user_id": "Actor; null for system actions.", "action": "Human-readable action.", "entity": "Entity type, e.g. connector, run, user.",
                  "entity_id": "Id of the changed entity (string so any key fits).", "before": "State before the change.", "after": "State after the change."},
    "runs": {"user_id": "Owner.", "module": "ai | pipeline | cioa (cioa = Competitive Insights).", "kind": "Search | Workflow run | Report generated (History filter).",
             "title": "Question truncated to 70 chars.", "question": "Full question as asked (or auto-built from pipeline filters).",
             "mode": "Standard | Deep Research.", "params": "Inputs: context, sources, filters, workflow, ...",
             "status": "In Progress | Paused | Failed | Completed | Cancelled.", "steps": "Loader stages with sub-items and statuses.",
             "step_index": "Current / resume step.", "step_label": "e.g. \"Step 2 of 5 - Search literature\".",
             "result": "While running: {_work}; when completed: the module result.", "error": "Failure message.",
             "source_count": "Evidence records retrieved.", "project": "Research session from the context (History project filter).",
             "saved": "True once saved to Workspace.", "approved": "Competitive Insights report approved.",
             "parent_run_id": "Previous turn this follow-up answers.", "thread_id": "Conversation id = id of the first turn (logical, no FK).",
             "notify": "\"Notify me when ready\".", "workflow": "Competitive Insights workflow key, e.g. competitive, endpoint.",
             "deleted": "Soft delete (Clear history, Home delete)."},
    "workspace_items": {"owner_id": "Owner (full control).", "title": "Name in the Workspace list.", "item_type": "Search | Question set | Report | Assessment | Pipeline view.",
                        "module": "ai | pipeline | cioa | scoring.", "folder": "Folder / project name.", "status": "Saved | Draft | Completed.",
                        "question": "Saved question.", "summary": "One-line summary in the details drawer.", "params": "Saved inputs incl. mode and workflow (for Run again).",
                        "snapshot": "Frozen result shown on reopen.", "source_count": "Sources used.", "archived": "Archived tab.",
                        "run_id": "Run it was saved from (nullable)."},
    "item_versions": {"item_id": "Parent item.", "label": "v1, v2 ...", "note": "e.g. \"Restored v1\".", "snapshot": "Snapshot at that version."},
    "item_shares": {"item_id": "Shared item.", "user_id": "Colleague (null when shared with a group).", "group": "Group name (null when shared with a user). Reserved word: quote it in SQL.",
                    "permission": "view | edit."},
    "item_notes": {"item_id": "Item commented on.", "user_id": "Author.", "text": "Note body."},
    "scoring_models": {"name": "Template name.", "version": "e.g. v2.1.", "status": "Draft | Published | Inactive. Only Published can be used.",
                       "criteria": "Criteria with scale definitions and default weights."},
    "assessments": {"owner_id": "Owner.", "model_id": "Template it was created from.", "title": "Assessment name.", "target": "Molecular target, e.g. IRAK4.",
                    "indications": "Indications being ranked.", "criteria": "Working copy of criteria (users can add / edit / remove).",
                    "weights": "Percent per criterion; must total 100.", "ai_scores": "AI-recommended 1-5 scores (null = insufficient evidence).",
                    "overrides": "Human overrides with justification.", "status": "Draft | Saved."},
    "scenarios": {"assessment_id": "Parent assessment.", "name": "Scenario name.", "weights": "Weights at save time.", "overrides": "Overrides at save time.",
                  "ranking": "Resulting ranking at save time."},
    "pipeline_assets": {"name": "Asset / program name.", "sponsor": "Company.", "modality": "Small molecule | Targeted protein degrader | Monoclonal antibody ...",
                        "target": "e.g. IRAK4 inhibitor.", "indication": "e.g. SLE.", "stage": "Discovery | Preclinical | Phase 1-3 | Filed | Marketed.",
                        "activity": "Recruiting | Active | Completed | Terminated | — (On hold / Discontinued filters match here).",
                        "geography": "US | EU | Global ...", "source": "Origin connector."},
    "connectors": {"category": "0-4 index into categories (Scientific, Clinical, Regulatory, Commercial, IP).", "name": "Unique source name.",
                   "description": "What it is used for.", "enabled": "Admin switch; disabled sources are never queried.", "health": "Healthy | Warning | Failed.",
                   "license": "Open | Licensed.", "last_sync": "Last successful test / sync.", "config": "Non-secret settings only.",
                   "secret_ref": "Pointer into the secrets vault. Never store credentials here."},
    "admin_records": {"kind": "workflow | template | prompt.", "name": "Record name.", "status": "Draft | Published | Inactive.", "version": "e.g. v3.",
                      "data": "Kind-specific fields (see JSON shapes)."},
}
COMMON = {"id": "Primary key.", "created_at": "Creation time (UTC).", "updated_at": "Last change (UTC, set by the app)."}

JSON_SHAPES = [
    ("users.context", '{"target": "IRAK4", "mechanism": "IRAK4 inhibition", "indications": ["SLE","RA","AD"], "geography": "United States", "session": "IRAK4 Inflammation Programme"}'),
    ("users.prefs", '{"deep_research": false, "in_app_notifications": true, "email": false}'),
    ("runs.params", '{"context": {...}, "sources": [..]|null, "resolved_sources": [..], "filters": {"target","indication","modality","stage"}, "more_filters": {"status","phase":[..],...}, "workflow": "competitive", "therapeutic_area": "...", "indications": [..], "molecular_target": "..."}'),
    ("runs.steps", '[{"name": "Search literature", "status": "pending|active|done|failed|skipped", "items": [{"label": "PubMed", "status": "done"}]}]'),
    ("runs.result (running)", '{"_work": {"activity": [..], "findings": [{"text","source","evidence":{"key","label"},"preliminary":true}], "records": [..], "errors": [..]}}'),
    ("runs.result (AI, completed)", '{"relevant": true, "answer": "...", "key_findings": [..], "top_sources": [{"name","url"}], "references": [..], "follow_ups": [..], "research_summary": {"stage_count","stages","sources_searched","elapsed_seconds"}}'),
    ("runs.result (Competitive Insights)", '{"workflow": {"key","name","short","icon"}, "context": {...}, "stats": [{"value","label","icon"}], "key_takeaways": [..], "sections": [{"index","title","bullets":[..],"table":null}], "data_sources": [..]}'),
    ("scoring_models.criteria / assessments.criteria", '[{"key": "unmet_need", "name": "Unmet Need", "rule": "1-5 based on ...", "scale": {"1": "...", "3": "...", "5": "..."}, "weight": 20}]'),
    ("assessments.weights", '{"population": 20, "unmet_need": 20, ...}   (must total 100)'),
    ("assessments.ai_scores", '{"SLE": {"population": 3, "unmet_need": 5, ...}, "RA": {...}}   (null = missing)'),
    ("assessments.overrides", '{"RA": {"population": {"score": 4, "justification": "...", "user_id": 2, "at": "ISO time"}}}'),
    ("admin_records.data", 'workflow: {"key","category","owner","subcategories"} · template: {"linked_workflow","workflow_key","sections"} · prompt: {"scope","variables","visibility"}'),
    ("audit_log.before / after", "Free-form snapshot of the changed entity (e.g. connector_out before and after an edit)."),
]

RELS = [  # child.fk, parent, cardinality, required, on delete (ORM behaviour), meaning
    ("runs.user_id", "users", "1 : N", "Yes", "Blocked (no cascade)", "A user owns many runs."),
    ("runs.parent_run_id", "runs", "0..1 : N", "No", "Blocked", "Follow-up turn → previous turn."),
    ("runs.thread_id (logical, ~FK)", "runs", "1 : N", "No", "Not enforced", "All turns of a conversation share the first turn's id."),
    ("workspace_items.owner_id", "users", "1 : N", "Yes", "Blocked", "A user owns many saved items."),
    ("workspace_items.run_id", "runs", "0..1 : N", "No", "Blocked", "Item saved from a run."),
    ("item_versions.item_id", "workspace_items", "1 : N", "Yes", "Cascade (ORM)", "Deleting an item deletes its versions."),
    ("item_shares.item_id", "workspace_items", "1 : N", "Yes", "Cascade (ORM)", "Deleting an item deletes its shares."),
    ("item_shares.user_id", "users", "0..1 : N", "No", "Blocked", "Share target when shared with a person."),
    ("item_notes.item_id", "workspace_items", "1 : N", "Yes", "Cascade (ORM)", "Deleting an item deletes its notes."),
    ("item_notes.user_id", "users", "1 : N", "Yes", "Blocked", "Note author."),
    ("assessments.owner_id", "users", "1 : N", "Yes", "Blocked", "A user owns many assessments."),
    ("assessments.model_id", "scoring_models", "1 : N", "Yes", "Blocked", "Template the assessment came from."),
    ("scenarios.assessment_id", "assessments", "1 : N", "Yes", "Cascade (ORM)", "Deleting an assessment deletes its scenarios."),
    ("notifications.user_id", "users", "1 : N", "Yes", "Blocked", "Recipient."),
    ("audit_log.user_id", "users", "0..1 : N", "No", "Blocked", "Actor (null = system)."),
    ("revoked_tokens.user_id", "users", "1 : N", "Yes", "Blocked", "Logged-out sessions of a user."),
]


# ------------------------------------------------------------------------------------------- helpers
def grid(data, widths, head=True, zebra=False):
    t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, BD), ("BOX", (0, 0), (-1, -1), 0.6, BD),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
          ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if head:
        st += [("BACKGROUND", (0, 0), (-1, 0), BG), ("LINEBELOW", (0, 0), (-1, 0), 0.8, BD)]
    t.setStyle(TableStyle(st))
    return t


def P(x, s=CELL):
    return Paragraph(x, s)


def default_of(c):
    if c.default is None:
        return ""
    a = c.default.arg
    if callable(a):
        name = getattr(getattr(a, "__wrapped__", a), "__name__", "fn")
        return {"now": "now (UTC)", "dict": "{}", "list": "[]", "default_context": "context template",
                "default_prefs": "prefs template"}.get(name, name)
    return "'—'" if a == "—" else repr(a)


def type_of(c):
    return str(c.type.compile(dialect=postgresql.dialect())).replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ")


def keys_of(c, t):
    k = []
    if c.primary_key:
        k.append("PK")
    for fk in c.foreign_keys:
        k.append("FK → " + fk.target_fullname)
    if c.unique:
        k.append("UNIQUE")
    if any(c in i.columns.values() for i in t.indexes):
        k.append("INDEX")
    return "<br/>".join(k)


def code(text, style=CODE):
    return Preformatted(text.strip("\n"), style)


# ------------------------------------------------------------------------------------------- ER diagram
BOX_W = 110
COLS = (14, 135, 256, 377)          # 4 columns; x=14 leaves room for the runs self-reference on the left
ROWS = (16, 206, 416)       # 16 pt top padding for the runs self-reference
LAYOUT = {  # table -> (column, row)
    "runs": (0, 0), "notifications": (1, 0), "audit_log": (2, 0), "revoked_tokens": (3, 0),
    "workspace_items": (0, 1), "users": (1, 1), "assessments": (2, 1), "scoring_models": (3, 1),
    "item_versions": (0, 2), "item_shares": (1, 2), "item_notes": (2, 2), "scenarios": (3, 2),
}
SHOW = {  # columns listed in each box (keys first, then notable fields)
    "users": ["id", "email", "role", "groups", "context"], "runs": ["id", "user_id", "parent_run_id", "thread_id", "module", "status"],
    "workspace_items": ["id", "owner_id", "run_id", "item_type", "snapshot"], "item_versions": ["id", "item_id", "label"],
    "item_shares": ["id", "item_id", "user_id", "group", "permission"], "item_notes": ["id", "item_id", "user_id"],
    "notifications": ["id", "user_id", "route", "unread"], "scoring_models": ["id", "name", "status"],
    "assessments": ["id", "owner_id", "model_id", "weights", "overrides"], "scenarios": ["id", "assessment_id", "ranking"],
    "audit_log": ["id", "user_id", "action", "entity"], "revoked_tokens": ["jti", "user_id", "expires_at"],
}
EDGES = [  # parent, child, label, parent side, child side, parent offset, child offset (pt along the side)
    ("users", "notifications", "receives", "top", "bottom", -30, 0),
    ("users", "audit_log", "actor", "top", "bottom", 12, 0),
    ("users", "revoked_tokens", "logged out", "top", "bottom", 38, -10),
    ("users", "runs", "owns", "left", "right", 18, 0),
    ("users", "workspace_items", "owns", "left", "right", -12, -12),
    ("users", "assessments", "owns", "right", "left", 0, 0),
    ("users", "item_shares", "shared with", "bottom", "top", 0, 0),
    ("users", "item_notes", "author", "bottom", "top", 35, -25),
    ("runs", "workspace_items", "run_id", "bottom", "top", 0, 0),
    ("workspace_items", "item_versions", "", "bottom", "top", -25, -25),
    ("workspace_items", "item_shares", "", "bottom", "top", 10, -35),
    ("workspace_items", "item_notes", "", "bottom", "top", 40, -45),
    ("scoring_models", "assessments", "template", "left", "right", 0, 0),
    ("assessments", "scenarios", "", "bottom", "top", 30, -20),
]
ROW_H, HEAD_H, PAD = 10.5, 15, 4


def box_h(n):
    return HEAD_H + n * ROW_H + PAD


def er_drawing():
    H = ROWS[-1] + box_h(6) + 4
    d = Drawing(493, H)
    geo = {}
    for name, (ci, ri) in LAYOUT.items():
        h = box_h(len(SHOW[name]))
        geo[name] = (COLS[ci], H - ROWS[ri] - h, BOX_W, h)  # reportlab origin is bottom-left

    def anchor(name, side, off=0.0):
        x, y, w, h = geo[name]
        return {"left": (x, y + h / 2 + off), "right": (x + w, y + h / 2 + off),
                "top": (x + w / 2 + off, y + h), "bottom": (x + w / 2 + off, y)}[side]

    # edges first so boxes sit on top
    for parent, child, label, ps, cs, po, co in EDGES:
        x1, y1 = anchor(parent, ps, po)
        x2, y2 = anchor(child, cs, co)
        d.add(Line(x1, y1, x2, y2, strokeColor=colors.HexColor("#9aa0a6"), strokeWidth=0.9))
        # cardinality markers: "1" near parent, "N" near child
        def near(xa, ya, xb, yb, t):
            return xa + (xb - xa) * t, ya + (yb - ya) * t
        short = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 < 30
        px, py = (x1 + (1 if x2 > x1 else -5), y1) if short else near(x1, y1, x2, y2, 0.08)
        cx, cy = (x2 + (-6 if x2 > x1 else 1), y2) if short else near(x1, y1, x2, y2, 0.90)
        d.add(String(px + (0 if short else 2), py + 2, "1", fontName="Helvetica-Bold", fontSize=6.5, fillColor=NAVY))
        d.add(String(cx + (0 if short else 2), cy + 2, "N", fontName="Helvetica-Bold", fontSize=6.5, fillColor=RED))
        if label and not short:
            mx, my = near(x1, y1, x2, y2, 0.5)
            d.add(String(mx + 3, my + 2, label, fontName="Helvetica-Oblique", fontSize=6.3, fillColor=MUT))
    # self reference runs.parent_run_id
    x, y, w, h = geo["runs"]
    ya, yb = y + h - HEAD_H - 3 * ROW_H + 3, y + h - HEAD_H - 4 * ROW_H + 3   # parent_run_id and thread_id rows
    for seg in ((x, ya, x - 10, ya), (x - 10, ya, x - 10, y + h + 6), (x - 10, y + h + 6, x + 20, y + h + 6),
                (x + 20, y + h + 6, x + 20, y + h)):
        d.add(Line(*seg, strokeColor=colors.HexColor("#9aa0a6"), strokeWidth=0.9))
    ln = Line(x, yb, x - 6, yb, strokeColor=colors.HexColor("#9aa0a6"), strokeWidth=0.9, strokeDashArray=[2, 2])
    d.add(ln)
    d.add(Line(x - 6, yb, x - 10, yb, strokeColor=colors.HexColor("#9aa0a6"), strokeWidth=0.9, strokeDashArray=[2, 2]))
    d.add(String(x + 24, y + h + 4, "self: parent_run_id (FK), thread_id (~FK)", fontName="Helvetica-Oblique",
                 fontSize=6.3, fillColor=MUT))
    for name, (x, y, w, h) in geo.items():
        col = DOMAIN_COL[DOMAIN[name]]
        d.add(Rect(x, y, w, h, fillColor=colors.white, strokeColor=col, strokeWidth=1, rx=4, ry=4))
        d.add(Rect(x, y + h - HEAD_H, w, HEAD_H, fillColor=col, strokeColor=col, rx=4, ry=4))
        d.add(Rect(x, y + h - HEAD_H, w, 5, fillColor=col, strokeColor=col))
        d.add(String(x + 6, y + h - HEAD_H + 4.5, name, fontName="Helvetica-Bold", fontSize=8, fillColor=colors.white))
        t = T[name]
        for i, cn in enumerate(SHOW[name]):
            c = t.c[cn]
            ty = y + h - HEAD_H - (i + 1) * ROW_H + 1
            tag = "PK" if c.primary_key else "FK" if c.foreign_keys else ""
            if cn == "thread_id":
                tag = "~FK"
            d.add(String(x + 6, ty, tag, fontName="Helvetica-Bold", fontSize=6.3, fillColor=RED if tag == "PK" else NAVY))
            d.add(String(x + 24, ty, cn, fontName="Courier", fontSize=7, fillColor=INK))
            d.add(String(x + w - 5, ty, type_of(c).split("(")[0].lower(), fontName="Helvetica", fontSize=6.3,
                         fillColor=MUT, textAnchor="end"))
    return d


def legend():
    cells, widths, style = [], [], [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 3),
                                    ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]
    for i, (n, c) in enumerate(DOMAIN_COL.items()):
        cells += ["", P(n)]
        widths += [3 * mm, 31.8 * mm]
        style.append(("BACKGROUND", (2 * i, 0), (2 * i, 0), c))
    t = Table([cells], colWidths=widths, rowHeights=[9])
    t.setStyle(TableStyle(style))
    return t


# ------------------------------------------------------------------------------------------- document
story = [
    Paragraph("Database ER Guide", H1),
    Paragraph(f"New Indication Intelligence Platform · Backend v0.3 · {date.today():%d %b %Y}<br/>"
              f"{len(T)} tables · generated from {mono('app/db/models.py')} (SQLAlchemy 2.0). "
              "Development uses SQLite; production targets PostgreSQL.", SUB),
    Paragraph("1. Tables at a glance", H2),
]
rows = [[P(h, HEAD) for h in ("Table", "Domain", "Columns", "Purpose")]]
for n, dom, purpose in TABLES:
    rows.append([P(mono(n), CMONO), P(dom), P(str(len(T[n].c))), P(purpose)])
story.append(grid(rows, (34 * mm, 34 * mm, 15 * mm, 91 * mm)))

story += [Paragraph("2. Conventions", H2), grid([[P(a, CELLB), P(b)] for a, b in [
    ("Keys", f"Integer surrogate {mono('id')} primary keys (SERIAL on PostgreSQL), except {mono('revoked_tokens.jti')}."),
    ("Timestamps", "Stored in UTC, set by the application (not database defaults). SQLite returns them without a time zone; "
                   "the app treats naive values as UTC."),
    ("Enumerations", "Stored as short strings (e.g. status, role, module) and validated by the API, not by DB constraints."),
    ("JSON columns", "Flexible, module-specific payloads (run results, context, scoring weights). Anything filtered or sorted on "
                     "is a real column. Use JSONB on PostgreSQL."),
    ("Soft delete", f"{mono('runs.deleted')} hides history rows; {mono('workspace_items.archived')} is the Archived tab. "
                    "Workspace item delete is a hard delete with an audit entry."),
    ("Delete behaviour", "No ON DELETE rules in the database. Child rows of workspace items (versions, shares, notes) and of "
                         "assessments (scenarios) are deleted by the ORM cascade. Deleting a user or run that is still "
                         "referenced fails; deactivate users instead."),
    ("Logical links", f"{mono('runs.thread_id')} groups conversation turns but has no foreign key (it can equal the row's own id)."),
    ("Reserved word", f"{mono('item_shares.group')} is a SQL keyword: write {mono(chr(34) + 'group' + chr(34))} in raw SQL."),
]], (30 * mm, 144 * mm), head=False), PageBreak()]

story += [Paragraph("3. Entity-relationship diagram", H2),
          Paragraph("Key columns and a few notable fields per table. <b>1</b> marks the parent side, <b>N</b> the child side; "
                    "users owns runs, workspace items and assessments. "
                    "~FK = logical reference without a constraint. pipeline_assets, connectors and admin_records have no foreign keys "
                    "and are listed in section 5.", INTRO),
          Spacer(1, 4), er_drawing(), Spacer(1, 6), legend(), PageBreak()]

story += [Paragraph("4. Relationships", H2)]
rows = [[P(h, HEAD) for h in ("Foreign key (child)", "Parent", "Cardinality", "Req.", "On parent delete", "Meaning")]]
for r in RELS:
    rows.append([P(mono(r[0]), CMONO), P(mono(r[1]), CMONO), P(r[2]), P(r[3]), P(r[4]), P(r[5])])
story.append(grid(rows, (47 * mm, 32 * mm, 17 * mm, 10 * mm, 24 * mm, 44 * mm)))

story += [Paragraph("5. Data dictionary", H2),
          Paragraph("Types are shown as PostgreSQL types (SQLite stores the same columns with its own affinities). "
                    "Null = column accepts NULL. Default = value applied by the application on insert.", INTRO)]
for name, dom, purpose in TABLES:
    t = T[name]
    story += [CondPageBreak(60 * mm), Paragraph(f"{name} <font size='8.5' color='#6e7076'>· {dom}</font>", H3), P(purpose, BODY)]
    rows = [[P(h, HEAD) for h in ("Column", "Type", "Null", "Default", "Keys", "Description")]]
    for c in t.c:
        desc = COL.get(name, {}).get(c.name) or COMMON.get(c.name, "")
        rows.append([P(mono(c.name), CMONO), P(type_of(c), CMONO), P("yes" if c.nullable else "no"), P(default_of(c), CMONO),
                     P(keys_of(c, t)), P(desc)])
    story.append(grid(rows, (30 * mm, 26 * mm, 10 * mm, 22 * mm, 30 * mm, 56 * mm)))
    idx = sorted(i.name for i in t.indexes)
    if idx:
        story.append(P(f"Indexes: {mono(', '.join(idx))}", ParagraphStyle("ix", parent=CELL, textColor=MUT, spaceBefore=2)))

story += [PageBreak(), Paragraph("6. JSON column shapes", H2),
          Paragraph("Shapes the application reads and writes. Keys not listed are ignored.", INTRO)]
rows = [[P(h, HEAD) for h in ("Column", "Shape / example")]]
for a, b in JSON_SHAPES:
    rows.append([P(mono(a), CMONO), P(mono(b.replace("<", "&lt;")), CMONO)])
story.append(grid(rows, (44 * mm, 130 * mm)))

# ------------------------------------------------------------------------------------------- commands
story += [PageBreak(), Paragraph("7. Database commands", H2),
          Paragraph("Run from the repository root with the virtual environment active.", INTRO),
          Paragraph("7.1 Configure the connection", H3),
          P(f"Set {mono('DATABASE_URL')} in {mono('.env')} (see {mono('.env.example')}). Default is the SQLite file "
            f"{mono('./gilead.db')}. {mono('SEED_DEMO_DATA=false')} disables demo seeding.", BODY),
          code("""
# SQLite (development)
DATABASE_URL=sqlite:///./gilead.db
# PostgreSQL (psycopg 3 driver, already in requirements.txt)
DATABASE_URL=postgresql+psycopg://gilead:<password>@localhost:5432/gilead
SEED_DEMO_DATA=false
"""),
          Paragraph("7.2 Create or upgrade the schema", H3),
          P("Starting the API does this automatically: it creates missing tables, adds columns and indexes introduced since the "
            "database was created (development helper), then seeds demo and reference data when enabled.", BODY),
          code("""
uvicorn app.main:app --reload

# or without starting the server
python -c "import app.db.models; from app.db.base import Base, engine, add_missing_columns; \\
Base.metadata.create_all(engine); print(add_missing_columns(engine))"
"""),
          Paragraph("7.3 Seed data", H3),
          code("""
python -c "import app.db.models; from app.db.base import SessionLocal; from app.seed import seed, seed_reference; \\
s = SessionLocal(); seed(s); seed_reference(s); s.close()"
"""),
          P(f"{mono('seed()')} runs only on an empty users table (demo users, connectors, assets, scoring model). "
            f"{mono('seed_reference()')} is idempotent: workflows, report templates, prompts. Demo password: ChangeMe123!", BODY),
          Paragraph("7.4 Reset the development database", H3),
          code("""
cp gilead.db gilead.db.bak            # optional backup
rm gilead.db                          # next start recreates and reseeds it
pytest                                # tests use their own ./test.db and delete it first
"""),
          Paragraph("7.5 Inspect with sqlite3", H3),
          code("""
sqlite3 gilead.db
.tables                                   -- list tables
.schema runs                              -- CREATE statement incl. indexes
PRAGMA table_info(runs);                  -- columns
PRAGMA index_list(runs);                  -- indexes
PRAGMA foreign_key_list(item_shares);     -- foreign keys
.mode box
.quit
"""),
          Paragraph("7.6 Useful queries", H3),
          code("""
-- Runs needing attention (Home "In Progress", History "Resume")
SELECT id, module, status, step_label, updated_at FROM runs
 WHERE deleted = 0 AND status IN ('In Progress','Paused','Failed') ORDER BY updated_at DESC;

-- All turns of a conversation
SELECT id, question, status, workflow FROM runs WHERE thread_id = :thread_id ORDER BY id;

-- Who can see a workspace item
SELECT s.permission, u.name, s."group" FROM item_shares s LEFT JOIN users u ON u.id = s.user_id
 WHERE s.item_id = :item_id;

-- Unread notifications per user
SELECT user_id, COUNT(*) FROM notifications WHERE unread = 1 GROUP BY user_id;

-- Recent admin changes
SELECT a.created_at, u.name, a.action FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
 ORDER BY a.id DESC LIMIT 50;

-- Enabled connectors that need attention
SELECT name, health, last_sync FROM connectors WHERE enabled = 1 AND health <> 'Healthy';

-- Housekeeping: purge revoked tokens that have expired anyway (also done on each logout)
DELETE FROM revoked_tokens WHERE expires_at < CURRENT_TIMESTAMP;
"""),
          P("On PostgreSQL write booleans as true/false instead of 1/0.", ParagraphStyle("n", parent=CELL, textColor=MUT)),
          Paragraph("7.7 PostgreSQL setup", H3),
          code("""
psql -U postgres -c "CREATE ROLE gilead LOGIN PASSWORD '<password>';"
psql -U postgres -c "CREATE DATABASE gilead OWNER gilead;"
psql "postgresql://gilead:<password>@localhost:5432/gilead" -c "\\dt"     # list tables
psql ... -c "\\d+ runs"                                                   # describe a table
"""),
          Paragraph("7.8 Migrations with Alembic (recommended for staging / production)", H3),
          P(f"{mono('add_missing_columns()')} only adds nullable or defaulted columns and missing indexes; it never renames, "
            "drops or changes types. Use Alembic for anything beyond development.", BODY),
          code("""
alembic init migrations
# migrations/env.py:  import app.db.models; from app.db.base import Base; target_metadata = Base.metadata
#                     config.set_main_option("sqlalchemy.url", settings.database_url)
alembic revision --autogenerate -m "baseline"
alembic upgrade head            # apply
alembic downgrade -1            # roll back one revision
alembic current                 # show the applied revision
alembic stamp head              # mark an existing database as up to date without running migrations
"""),
          Paragraph("7.9 Backup and restore", H3),
          code("""
# SQLite
sqlite3 gilead.db ".backup 'gilead-$(date +%F).db'"
cp gilead-2026-11-20.db gilead.db                     # restore (stop the API first)

# PostgreSQL
pg_dump -Fc -d gilead -f gilead-$(date +%F).dump
pg_restore --clean --if-exists -d gilead gilead-2026-11-20.dump
"""),
          Paragraph("7.10 Moving from SQLite to PostgreSQL", H3),
          P(f"Create the schema on PostgreSQL (7.2 with the new {mono('DATABASE_URL')}, {mono('SEED_DEMO_DATA=false')}), then copy "
            "rows table by table in the order of section 1's foreign keys (users, scoring_models, connectors, pipeline_assets, "
            "admin_records, runs, workspace_items and children, assessments, scenarios, notifications, audit_log, "
            f"revoked_tokens). Afterwards reset each sequence, e.g. "
            f"{mono("SELECT setval(pg_get_serial_sequence('runs', 'id'), MAX(id)) FROM runs;")},"" and change JSON columns to JSONB.", BODY)]

# ------------------------------------------------------------------------------------------- DDL appendix
pg = postgresql.dialect()
ddl = []
for t in Base.metadata.sorted_tables:
    ddl.append(str(CreateTable(t).compile(dialect=pg)).strip() + ";")
    for i in sorted(t.indexes, key=lambda i: i.name):
        ddl.append(str(CreateIndex(i).compile(dialect=pg)).strip() + ";")
story += [PageBreak(), Paragraph("Appendix A · PostgreSQL DDL", H2),
          Paragraph("Generated from the models in dependency order. Equivalent to what create_all() emits on PostgreSQL. "
                    "For production, replace JSON with JSONB and manage changes with Alembic.", INTRO),
          code("\n\n".join(ddl), DDL)]


def deco(c, d):
    c.saveState()
    c.setFillColor(RED)
    c.rect(0, A4[1] - 5, A4[0], 5, fill=1, stroke=0)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(MUT)
    c.drawString(18 * mm, 10 * mm, "Database ER Guide · Backend v0.3")
    c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {d.page}")
    c.restoreState()


SimpleDocTemplate(OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                  title="Database ER Guide", author="Backend team").build(story, onFirstPage=deco, onLaterPages=deco)
print("ok", OUT)
