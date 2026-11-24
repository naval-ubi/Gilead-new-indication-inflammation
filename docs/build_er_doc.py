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
DOMAIN_COL = {"Identity & access": colors.HexColor("#203662"), "Research runs": colors.HexColor("#c5203f"),
              "Workspace": colors.HexColor("#0f766e"), "Prioritization scoring": colors.HexColor("#b45309"),
              "Pipeline catalogue": colors.HexColor("#6d28d9"), "Configuration": colors.HexColor("#6e7076")}

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
    ("users", "Identity & access", "People who sign in. Role and groups are rows in their own tables; the header research context and preferences are per-user JSON."),
    ("roles", "Identity & access", "Admin, Analyst, Reviewer, Viewer. Each role grants a set of permissions."),
    ("permissions", "Identity & access", "Actions a role can be granted: view, create, edit, execute, publish, share, administer."),
    ("role_permissions", "Identity & access", "Link table: which permissions each role grants (many-to-many)."),
    ("groups", "Identity & access", "Teams used for sharing (Commercial, Clinical, R&D, Market Access ...)."),
    ("user_groups", "Identity & access", "Link table: group membership (many-to-many)."),
    ("revoked_tokens", "Identity & access", "Login tokens ended by POST /auth/logout. Checked on every authenticated request."),
    ("notifications", "Identity & access", "Bell notifications: research completed/failed, item shared with you."),
    ("audit_log", "Identity & access", "Who changed what and when (admin edits, overrides, approvals, deletes, logouts) with before/after JSON."),
    ("runs", "Research runs", "One execution of AI Scientific Search, Drug Pipeline Explorer or Competitive Insights. Steps, progress, result, conversation thread."),
    ("workspace_items", "Workspace", "Saved research (search, report, pipeline view, assessment). Holds a frozen snapshot; reopening never reruns."),
    ("item_versions", "Workspace", "Version history of a workspace item (v1, v2 ...) with the snapshot of each version, for Restore."),
    ("item_shares", "Workspace", "Who an item is shared with: one user or one group, with view or edit permission."),
    ("item_notes", "Workspace", "Notes and comments on a workspace item."),
    ("scoring_models", "Prioritization scoring", "Admin-managed scoring templates: criteria, 1-5 scale definitions, default weights."),
    ("assessments", "Prioritization scoring", "A user's working copy of a scoring model for a target and indications: criteria, weights, AI scores."),
    ("assessment_overrides", "Prioritization scoring", "Human overrides of AI scores: one row per indication and criterion, with justification, author and time."),
    ("scenarios", "Prioritization scoring", "Named snapshots of an assessment's weights and overrides with the resulting ranking."),
    ("sponsors", "Pipeline catalogue", "Companies developing drug assets."),
    ("modalities", "Pipeline catalogue", "Small molecule, targeted protein degrader, monoclonal antibody ..."),
    ("targets", "Pipeline catalogue", "Molecular target / mechanism, e.g. IRAK4 inhibitor, IRAK4 degrader."),
    ("indications", "Pipeline catalogue", "Diseases, with a short code used across the app (SLE) and a full name."),
    ("drug_assets", "Pipeline catalogue", "A drug / program asset with its sponsor, modality and target, stored once."),
    ("pipeline_programs", "Pipeline catalogue", "One asset in one indication (and geography): development stage and trial activity. Behind the Pipeline Explorer and Competitive Insights statistics."),
    ("connectors", "Configuration", "Data source registry (PubMed, Citeline ...): enabled switch, health, licence. Secrets live in a vault."),
    ("admin_records", "Configuration", "Generic admin-managed rows: AI workflows, report templates, prompts library."),
]
DOMAIN = {n: d for n, d, _ in TABLES}
assert set(DOMAIN) == set(T), f"describe every table: missing {set(T) - set(DOMAIN)}, stale {set(DOMAIN) - set(T)}"

COL = {
    "users": {"name": "Display name (editable via PATCH /me/profile).", "email": "Login; unique, stored lower-case.",
              "password_hash": "scrypt hash (salt$hash). Empty when SSO is used.", "role_id": "The user's role; permissions come from the role.",
              "status": "Active | Inactive. Inactive users cannot sign in.", "last_login": "Set on each successful login.",
              "context": "Header research context; default for every new run.", "prefs": "Preferences dialog values."},
    "roles": {"name": "Admin | Analyst | Reviewer | Viewer.", "description": "Shown in the admin role list."},
    "permissions": {"code": "view | create | edit | execute | publish | share | administer. Checked by the API.",
                    "description": "What the permission allows."},
    "role_permissions": {"role_id": "Role.", "permission_id": "Permission the role grants."},
    "groups": {"name": "Unique group name.", "description": "Optional."},
    "user_groups": {"user_id": "Member.", "group_id": "Group."},
    "revoked_tokens": {"jti": "Token id from the JWT jti claim.", "user_id": "Owner of the token.",
                       "expires_at": "Token's own expiry; rows after this time can be purged.", "revoked_at": "When the user logged out."},
    "notifications": {"user_id": "Recipient.", "title": "Bold first line.", "body": "Detail line.",
                      "route": "Screen to open on click: ai | pipeline | cioa | workspace | home ...", "unread": "Drives the bell badge."},
    "audit_log": {"user_id": "Actor; null for system actions.", "action": "Human-readable action.", "entity": "Entity type, e.g. connector, run, user, group.",
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
    "item_shares": {"item_id": "Shared item.", "user_id": "Colleague, when shared with a person.", "group_id": "Group, when shared with a group.",
                    "permission": "view | edit."},
    "item_notes": {"item_id": "Item commented on.", "user_id": "Author.", "text": "Note body."},
    "scoring_models": {"name": "Template name.", "version": "e.g. v2.1.", "status": "Draft | Published | Inactive. Only Published can be used.",
                       "criteria": "Criteria with scale definitions and default weights."},
    "assessments": {"owner_id": "Owner.", "model_id": "Template it was created from.", "title": "Assessment name.", "target": "Molecular target, e.g. IRAK4.",
                    "indications": "Indications being ranked (codes, e.g. SLE).", "criteria": "Working copy of criteria (users can add / edit / remove).",
                    "weights": "Percent per criterion; must total 100.", "ai_scores": "AI-recommended 1-5 scores (null = Missing; the AI service cannot score yet).",
                    "status": "Draft | Saved."},
    "assessment_overrides": {"assessment_id": "Assessment.", "indication": "Indication code from the assessment.",
                             "criterion_key": "Criterion key from the assessment.", "score": "1-5; null = marked as Missing.",
                             "justification": "Required reason for the override.", "user_id": "Who overrode the score.",
                             "created_at": "When it was set (UTC)."},
    "scenarios": {"assessment_id": "Parent assessment.", "name": "Scenario name.", "weights": "Weights at save time.",
                  "overrides": "Snapshot of the overrides at save time (restored on Load).", "ranking": "Resulting ranking at save time."},
    "sponsors": {"name": "Company name; unique."}, "modalities": {"name": "Modality; unique."}, "targets": {"name": "Target / mechanism; unique."},
    "indications": {"code": "Short code used in filters, contexts and assessments, e.g. SLE.", "name": "Full name, e.g. Systemic Lupus Erythematosus."},
    "drug_assets": {"name": "Asset / program name (unique per sponsor).", "sponsor_id": "Developer.", "modality_id": "Modality.", "target_id": "Target / mechanism."},
    "pipeline_programs": {"asset_id": "Asset.", "indication_id": "Indication.", "stage": "Discovery | Preclinical | Phase 1-3 | Filed | Marketed.",
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
    ("runs.result (AI, completed)", '{"relevant": true, "answer": "...", "key_findings": [..], "top_sources": [{"name","url"}], "references": [..], "follow_ups": [], "provider": "ai_scientific_search", "research_summary": {"stage_count","stages","sources_searched","elapsed_seconds"}}'),
    ("runs.result (Competitive Insights)", '{"workflow": {"key","name","short","icon"}, "context": {...}, "stats": [{"value","label","icon"}], "key_takeaways": [..], "sections": [{"index","title","bullets":[..],"table":null}], "data_sources": [..]}'),
    ("scoring_models.criteria / assessments.criteria", '[{"key": "unmet_need", "name": "Unmet Need", "rule": "1-5 based on ...", "scale": {"1": "...", "3": "...", "5": "..."}, "weight": 20}]'),
    ("assessments.weights", '{"population": 20, "unmet_need": 20, ...}   (must total 100)'),
    ("assessments.ai_scores", '{"SLE": {"population": 3, "unmet_need": 5, ...}, "RA": {...}}   (null = missing)'),
    ("scenarios.overrides (snapshot)", '{"RA": {"population": {"score": 4, "justification": "...", "user_id": 2, "at": "ISO time"}}}  (copied from assessment_overrides on save)'),
    ("admin_records.data", 'workflow: {"key","category","owner","subcategories"} · template: {"linked_workflow","workflow_key","sections"} · prompt: {"scope","variables","visibility"}'),
    ("audit_log.before / after", "Free-form snapshot of the changed entity (e.g. connector_out before and after an edit)."),
]

RELS = [  # child.fk, parent, cardinality, required, on delete (ORM behaviour), meaning
    ("users.role_id", "roles", "1 : N", "Yes", "Blocked", "Every user has exactly one role."),
    ("role_permissions.role_id", "roles", "M : N", "Yes", "Blocked", "Permissions a role grants (link table)."),
    ("role_permissions.permission_id", "permissions", "M : N", "Yes", "Blocked", "Roles granting a permission (link table)."),
    ("user_groups.user_id", "users", "M : N", "Yes", "Blocked", "Groups a user belongs to (link table)."),
    ("user_groups.group_id", "groups", "M : N", "Yes", "Blocked", "Members of a group; API refuses to delete a non-empty group."),
    ("runs.user_id", "users", "1 : N", "Yes", "Blocked", "A user owns many runs."),
    ("runs.parent_run_id", "runs", "0..1 : N", "No", "Blocked", "Follow-up turn → previous turn."),
    ("runs.thread_id (logical, ~FK)", "runs", "1 : N", "No", "Not enforced", "All turns of a conversation share the first turn's id."),
    ("workspace_items.owner_id", "users", "1 : N", "Yes", "Blocked", "A user owns many saved items."),
    ("workspace_items.run_id", "runs", "0..1 : N", "No", "Blocked", "Item saved from a run."),
    ("item_versions.item_id", "workspace_items", "1 : N", "Yes", "Cascade (ORM)", "Deleting an item deletes its versions."),
    ("item_shares.item_id", "workspace_items", "1 : N", "Yes", "Cascade (ORM)", "Deleting an item deletes its shares."),
    ("item_shares.user_id", "users", "0..1 : N", "No", "Blocked", "Share target when shared with a person."),
    ("item_shares.group_id", "groups", "0..1 : N", "No", "Blocked", "Share target when shared with a group; API refuses to delete a group still used."),
    ("item_notes.item_id", "workspace_items", "1 : N", "Yes", "Cascade (ORM)", "Deleting an item deletes its notes."),
    ("item_notes.user_id", "users", "1 : N", "Yes", "Blocked", "Note author."),
    ("assessments.owner_id", "users", "1 : N", "Yes", "Blocked", "A user owns many assessments."),
    ("assessments.model_id", "scoring_models", "1 : N", "Yes", "Blocked", "Template the assessment came from."),
    ("assessment_overrides.assessment_id", "assessments", "1 : N", "Yes", "Cascade (ORM)", "Deleting an assessment deletes its overrides."),
    ("assessment_overrides.user_id", "users", "1 : N", "Yes", "Blocked", "Who overrode the score."),
    ("scenarios.assessment_id", "assessments", "1 : N", "Yes", "Cascade (ORM)", "Deleting an assessment deletes its scenarios."),
    ("drug_assets.sponsor_id", "sponsors", "1 : N", "Yes", "Blocked", "A sponsor develops many assets."),
    ("drug_assets.modality_id", "modalities", "1 : N", "Yes", "Blocked", "Modality of the asset."),
    ("drug_assets.target_id", "targets", "1 : N", "Yes", "Blocked", "Target / mechanism of the asset."),
    ("pipeline_programs.asset_id", "drug_assets", "1 : N", "Yes", "Blocked", "An asset is developed in many indications."),
    ("pipeline_programs.indication_id", "indications", "1 : N", "Yes", "Blocked", "Programs studying an indication."),
    ("notifications.user_id", "users", "1 : N", "Yes", "Blocked", "Recipient."),
    ("audit_log.user_id", "users", "0..1 : N", "No", "Blocked", "Actor (null = system)."),
    ("revoked_tokens.user_id", "users", "1 : N", "Yes", "Blocked", "Logged-out sessions of a user."),
]

NORMALISED = [  # before -> after, why
    ("users.role (text)", "roles, permissions, role_permissions; users.role_id",
     "Permissions were hard-coded per role name in code. They are now data, so an admin can change what a role may do "
     "(PUT /admin/roles/{id}/permissions) without a release, and a typo in a role name is impossible."),
    ("users.groups (JSON list)", "groups, user_groups",
     "Groups exist once, can be renamed in one place, listed with member counts, and joined in SQL."),
    ("item_shares.group (text)", "item_shares.group_id → groups",
     "A share now points at a real group: renaming a group keeps its shares, and unknown group names are rejected."),
    ("pipeline_assets (one wide table)", "sponsors, modalities, targets, indications, drug_assets, pipeline_programs",
     "Sponsor, modality, target and indication names were repeated on every row, and an asset was repeated once per "
     "indication. Each fact is now stored once; connector imports can de-duplicate sponsors and assets."),
    ("assessments.overrides (JSON)", "assessment_overrides",
     "Overrides are audit-relevant and carried a user_id inside JSON. They are now rows with a real foreign key to the user, "
     "one per indication and criterion (unique)."),
]
KEPT = [  # field, why it stays as is
    ("users.context, users.prefs", "Per-user settings read and written as a whole; nothing joins on them."),
    ("runs.params / steps / result, workspace_items.snapshot, item_versions.snapshot",
     "Snapshots: they must keep what was true at the time, even if reference data changes later."),
    ("scoring_models.criteria, assessments.criteria / weights / ai_scores",
     "A template and its working copy, edited and validated as one document; the scoring engine works on the whole set."),
    ("scenarios.weights / overrides / ranking", "Saved snapshots of an assessment, restored as a whole."),
    ("assessments.indications, assessment_overrides.indication", "Codes copied from the user's context when the assessment "
     "is created; the assessment keeps them even if the indication list changes. Codes match indications.code."),
    ("status, module, kind, mode, stage, activity, permission", "Small fixed vocabularies validated by the API. A lookup "
     "table would add joins without adding information. Can move to CHECK constraints on PostgreSQL."),
    ("connectors.category", "Five fixed categories defined in code; candidate for a lookup table if admins need to add categories."),
    ("runs.project, workspace_items.folder", "Free-text labels today; candidate for a folders table if folders get permissions or rename."),
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
ROW_GAP = 64
SHOW = {  # columns listed in each box (keys first, then notable fields)
    "users": ["id", "role_id", "email", "status", "context"], "roles": ["id", "name"], "permissions": ["id", "code"],
    "role_permissions": ["role_id", "permission_id"], "groups": ["id", "name"], "user_groups": ["user_id", "group_id"],
    "notifications": ["id", "user_id", "route", "unread"], "audit_log": ["id", "user_id", "action", "entity"],
    "revoked_tokens": ["jti", "user_id", "expires_at"],
    "runs": ["id", "user_id", "parent_run_id", "thread_id", "module", "status"],
    "workspace_items": ["id", "owner_id", "run_id", "item_type", "snapshot"], "item_versions": ["id", "item_id", "label"],
    "item_shares": ["id", "item_id", "user_id", "group_id", "permission"], "item_notes": ["id", "item_id", "user_id"],
    "scoring_models": ["id", "name", "status"], "assessments": ["id", "owner_id", "model_id", "weights", "ai_scores"],
    "assessment_overrides": ["id", "assessment_id", "user_id", "indication", "criterion_key", "score"],
    "scenarios": ["id", "assessment_id", "ranking"],
    "sponsors": ["id", "name"], "modalities": ["id", "name"], "targets": ["id", "name"], "indications": ["id", "code", "name"],
    "drug_assets": ["id", "name", "sponsor_id", "modality_id", "target_id"],
    "pipeline_programs": ["id", "asset_id", "indication_id", "stage", "activity"],
}
DIAGRAMS = [  # title, intro, layout {table: (col, row)}, edges (parent, child, label, parent side, child side, offsets)
    ("4.1 Identity & access", "Roles grant permissions through role_permissions; users belong to groups through user_groups.",
     {"permissions": (0, 0), "role_permissions": (1, 0), "roles": (2, 0),
      "groups": (0, 1), "user_groups": (1, 1), "users": (2, 1),
      "notifications": (1, 2), "audit_log": (2, 2), "revoked_tokens": (3, 2)},
     [("permissions", "role_permissions", "", "right", "left", 0, 0), ("roles", "role_permissions", "", "left", "right", 0, 0),
      ("roles", "users", "has role", "bottom", "top", 0, 0), ("groups", "user_groups", "", "right", "left", 0, 0),
      ("users", "user_groups", "", "left", "right", 0, 0),
      ("users", "notifications", "receives", "bottom", "top", -30, 0), ("users", "audit_log", "actor", "bottom", "top", 0, 0),
      ("users", "revoked_tokens", "logged out", "bottom", "top", 30, 0)]),
    ("4.2 Research runs & workspace", "users owns runs and workspace items; an item is shared with a user or a group.",
     {"runs": (0, 0), "users": (1, 0), "groups": (3, 0), "workspace_items": (0, 1),
      "item_versions": (0, 2), "item_notes": (1, 2), "item_shares": (2, 2)},
     [("users", "runs", "owns", "left", "right", 10, 10), ("users", "workspace_items", "owns", "bottom", "right", -35, 0),
      ("runs", "workspace_items", "run_id", "bottom", "top", 0, 0),
      ("workspace_items", "item_versions", "", "bottom", "top", -20, -20),
      ("workspace_items", "item_notes", "", "bottom", "top", 15, -25), ("workspace_items", "item_shares", "", "bottom", "top", 40, -35),
      ("users", "item_notes", "author", "bottom", "top", 0, 0), ("users", "item_shares", "shared with", "bottom", "top", 35, 0),
      ("groups", "item_shares", "shared with", "bottom", "top", 0, 30)]),
    ("4.3 Prioritization scoring", "An assessment is a working copy of a scoring model; each override is a row with its author.",
     {"users": (0, 0), "assessments": (1, 0), "scoring_models": (2, 0), "assessment_overrides": (1, 1), "scenarios": (2, 1)},
     [("users", "assessments", "", "right", "left", 0, 0), ("scoring_models", "assessments", "", "left", "right", 0, 0),
      ("assessments", "assessment_overrides", "", "bottom", "top", -20, -20), ("assessments", "scenarios", "", "bottom", "top", 30, -20),
      ("users", "assessment_overrides", "author", "bottom", "left", 0, 0)]),
    ("4.4 Pipeline catalogue", "An asset is stored once with its sponsor, modality and target; each indication it is developed in is a program.",
     {"sponsors": (0, 0), "modalities": (1, 0), "targets": (2, 0), "drug_assets": (1, 1), "indications": (3, 1),
      "pipeline_programs": (2, 2)},
     [("sponsors", "drug_assets", "", "bottom", "top", 0, -30), ("modalities", "drug_assets", "", "bottom", "top", 0, 0),
      ("targets", "drug_assets", "", "bottom", "top", 0, 30), ("drug_assets", "pipeline_programs", "", "bottom", "top", 0, -20),
      ("indications", "pipeline_programs", "", "bottom", "top", 0, 20)]),
]
ROW_H, HEAD_H, PAD = 10.5, 15, 4


def box_h(n):
    return HEAD_H + n * ROW_H + PAD


def er_drawing(layout: dict, edges: list):
    nrows = max(r for _, r in layout.values()) + 1
    row_h = [max([box_h(len(SHOW[n])) for n, (_, r) in layout.items() if r == ri] or [0]) for ri in range(nrows)]
    tops, y = [], (16 if "runs" in layout else 4)     # room above for the runs self-reference
    for h in row_h:
        tops.append(y)
        y += h + ROW_GAP
    H = y - ROW_GAP + 4
    d = Drawing(493, H)
    geo = {}
    for name, (ci, ri) in layout.items():
        h = box_h(len(SHOW[name]))
        geo[name] = (COLS[ci], H - tops[ri] - h, BOX_W, h)  # reportlab origin is bottom-left

    def anchor(name, side, off=0.0):
        x, y, w, h = geo[name]
        return {"left": (x, y + h / 2 + off), "right": (x + w, y + h / 2 + off),
                "top": (x + w / 2 + off, y + h), "bottom": (x + w / 2 + off, y)}[side]

    # edges first so boxes sit on top
    for parent, child, label, ps, cs, po, co in edges:
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
    # self reference runs.parent_run_id (only in the diagram that shows runs)
    if "runs" in geo:
        x, y, w, h = geo["runs"]
        grey = colors.HexColor("#9aa0a6")
        ya, yb = y + h - HEAD_H - 3 * ROW_H + 3, y + h - HEAD_H - 4 * ROW_H + 3   # parent_run_id and thread_id rows
        for seg in ((x, ya, x - 10, ya), (x - 10, ya, x - 10, y + h + 6), (x - 10, y + h + 6, x + 20, y + h + 6),
                    (x + 20, y + h + 6, x + 20, y + h)):
            d.add(Line(*seg, strokeColor=grey, strokeWidth=0.9))
        d.add(Line(x, yb, x - 10, yb, strokeColor=grey, strokeWidth=0.9, strokeDashArray=[2, 2]))
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
    items = list(DOMAIN_COL.items())
    rows, style = [], [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 3),
                       ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]
    for r in range(0, len(items), 3):
        row = []
        for k, (n, c) in enumerate(items[r:r + 3]):
            row += ["", P(n)]
            style.append(("BACKGROUND", (2 * k, r // 3), (2 * k, r // 3), c))
        rows.append(row + [""] * (6 - len(row)))
    t = Table(rows, colWidths=[3 * mm, 52 * mm] * 3, rowHeights=[10] * len(rows), hAlign="LEFT")
    t.setStyle(TableStyle(style))
    return t


# ------------------------------------------------------------------------------------------- document
story = [
    Paragraph("Database ER Guide", H1),
    Paragraph(f"New Indication Intelligence Platform · Backend v0.4 · {date.today():%d %b %Y}<br/>"
              f"{len(T)} tables. "
              "Development uses SQLite; production targets PostgreSQL.", SUB),
    Paragraph("1. Tables at a glance", ParagraphStyle("H2s", parent=H2, keepWithNext=0)),
]
rows = [[P(h, HEAD) for h in ("Table", "Domain", "Cols", "Purpose")]]
for n, dom, purpose in TABLES:
    rows.append([P(mono(n), CMONO), P(dom), P(str(len(T[n].c))), P(purpose)])
story.append(grid(rows, (39 * mm, 33 * mm, 14 * mm, 88 * mm)))

story += [Paragraph("2. Conventions", H2), grid([[P(a, CELLB), P(b)] for a, b in [
    ("Keys", f"Integer surrogate {mono('id')} primary keys (SERIAL on PostgreSQL). Exceptions: {mono('revoked_tokens.jti')}, and "
             f"the link tables {mono('role_permissions')} and {mono('user_groups')}, which use composite primary keys."),
    ("Uniqueness", "Names of roles, permissions, groups, sponsors, modalities, targets and indication codes are unique. "
                   "An asset is unique per sponsor; a program per asset, indication and geography; an override per "
                   "assessment, indication and criterion."),
    ("Timestamps", "Stored in UTC, set by the application (not database defaults). SQLite returns them without a time zone; "
                   "the app treats naive values as UTC."),
    ("Enumerations", "Small fixed vocabularies (status, module, stage, permission level) are short strings validated by the API."),
    ("JSON columns", "Flexible, module-specific payloads (run results, context, scoring weights). Anything filtered or sorted on "
                     "is a real column. They are JSONB on PostgreSQL."),
    ("Soft delete", f"{mono('runs.deleted')} hides history rows; {mono('workspace_items.archived')} is the Archived tab. "
                    "Workspace item delete is a hard delete with an audit entry."),
    ("Delete behaviour", "No ON DELETE rules in the database. Child rows of workspace items (versions, shares, notes) and of "
                         "assessments (overrides, scenarios) are deleted by the ORM cascade. Deleting a user, run, group or "
                         "lookup row that is still referenced fails; deactivate users, and empty a group before deleting it."),
    ("Logical links", f"{mono('runs.thread_id')} groups conversation turns but has no foreign key (it can equal the row's own id)."),
    ("Upgrades", "On PostgreSQL the API applies Alembic migrations on start-up (section 8.8). On SQLite (local "
                 f"development) {mono('app/db/upgrade.py')} creates new tables and converts older databases in place. Both "
                 "make sure roles, permissions and indications exist."),
]], (30 * mm, 144 * mm), head=False), PageBreak()]

story += [Paragraph("3. Normalisation decisions (v0.4)", H2),
          Paragraph("What moved into its own tables, and what deliberately stays as JSON or text. Databases created by "
                    "earlier versions are converted automatically on start-up, keeping their data.", INTRO)]
rows = [[P(h, HEAD) for h in ("Before", "After", "Why")]]
for a, b, why in NORMALISED:
    rows.append([P(mono(a), CMONO), P(mono(b), CMONO), P(why)])
story.append(grid(rows, (38 * mm, 50 * mm, 86 * mm)))
story += [Paragraph("Kept as JSON or text, on purpose", H3)]
rows = [[P(h, HEAD) for h in ("Field(s)", "Reason")]]
for a, why in KEPT:
    rows.append([P(mono(a), CMONO), P(why)])
story += [grid(rows, (60 * mm, 114 * mm)), PageBreak()]

story += [Paragraph("4. Entity-relationship diagrams", H2),
          Paragraph("One diagram per domain. Key columns and a few notable fields per table. <b>1</b> marks the parent side, "
                    "<b>N</b> the child side; link tables sit between the two tables they connect. ~FK = logical reference "
                    "without a constraint. users and groups appear in more than one diagram. connectors and admin_records "
                    "have no foreign keys (see section 6).", INTRO), legend()]
for i, (title, intro, layout, edges) in enumerate(DIAGRAMS):
    drawing = er_drawing(layout, edges)
    story += [CondPageBreak(drawing.height + 45), Paragraph(title, H3), P(intro, BODY), Spacer(1, 4), drawing, Spacer(1, 8)]
story.append(PageBreak())

story += [Paragraph("5. Relationships", H2)]
rows = [[P(h, HEAD) for h in ("Foreign key (child)", "Parent", "Card.", "Req.", "On parent delete", "Meaning")]]
def fk_label(x):
    return x.replace(".", ".<br/>", 1) if len(x.split(" ")[0]) > 26 else x


for r in RELS:
    rows.append([P(mono(fk_label(r[0])), CMONO), P(mono(r[1]), CMONO), P(r[2]), P(r[3]), P(r[4]), P(r[5])])
story.append(grid(rows, (47 * mm, 32 * mm, 17 * mm, 10 * mm, 24 * mm, 44 * mm)))

story += [Paragraph("6. Data dictionary", H2),
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

story += [PageBreak(), Paragraph("7. JSON column shapes", H2),
          Paragraph("Shapes the application reads and writes. Keys not listed are ignored.", INTRO)]
rows = [[P(h, HEAD) for h in ("Column", "Shape / example")]]
for a, b in JSON_SHAPES:
    rows.append([P(mono(a), CMONO), P(mono(b.replace("<", "&lt;")), CMONO)])
story.append(grid(rows, (44 * mm, 130 * mm)))

# ------------------------------------------------------------------------------------------- commands
story += [PageBreak(), Paragraph("8. Database commands", H2),
          Paragraph("Run from the repository root with the virtual environment active.", INTRO),
          Paragraph("8.1 Configure the connection", H3),
          P(f"Set {mono('DATABASE_URL')} in {mono('.env')} (see {mono('.env.example')}). Default is the SQLite file "
            f"{mono('./gilead.db')}. {mono('SEED_DEMO_DATA=false')} disables demo seeding.", BODY),
          code("""
# SQLite (development)
DATABASE_URL=sqlite:///./gilead.db
# PostgreSQL (psycopg 3 driver, already in requirements.txt)
DATABASE_URL=postgresql+psycopg://gilead:<password>@localhost:5432/gilead
SEED_DEMO_DATA=false
"""),
          Paragraph("8.2 Create or upgrade the schema", H3),
          P("Starting the API does this automatically. On PostgreSQL it applies the Alembic migrations (8.8). On SQLite it "
            f"uses {mono('upgrade_schema()')} in {mono('app/db/upgrade.py')}, which "
            "creates missing tables, makes sure roles, permissions and indications exist (also in production), converts "
            "databases from earlier versions in place (role/groups, shares, pipeline assets, overrides), and adds newer "
            "columns and indexes. It is safe to run repeatedly. Demo and workflow data are seeded afterwards when enabled.", BODY),
          code("""
uvicorn app.main:app --reload

# or without starting the server
python -c "import app.db.models; from app.db.base import engine; from app.db.upgrade import upgrade_schema; \\
upgrade_schema(engine)"
"""),
          Paragraph("8.3 Seed data", H3),
          code("""
python -c "import app.db.models; from app.db.base import SessionLocal; from app.seed import seed, seed_reference; \\
s = SessionLocal(); seed(s); seed_reference(s); s.close()"
"""),
          P(f"{mono('seed()')} runs only on an empty users table (demo users and groups, connectors, pipeline assets and programs, "
            "scoring model). Run it after the upgrade step, which creates the roles it uses. "
            f"{mono('seed_reference()')} is idempotent: workflows, report templates, prompts. Demo password: ChangeMe123!", BODY),
          Paragraph("8.4 Reset the development database", H3),
          code("""
cp gilead.db gilead.db.bak            # optional backup
rm gilead.db                          # next start recreates and reseeds it
pytest                                # tests use their own ./test.db and delete it first
"""),
          Paragraph("8.5 Inspect with sqlite3", H3),
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
          Paragraph("8.6 Useful queries", H3),
          code("""
-- Runs needing attention (Home "In Progress", History "Resume")
SELECT id, module, status, step_label, updated_at FROM runs
 WHERE deleted = 0 AND status IN ('In Progress','Paused','Failed') ORDER BY updated_at DESC;

-- All turns of a conversation
SELECT id, question, status, workflow FROM runs WHERE thread_id = :thread_id ORDER BY id;

-- Who can see a workspace item (people and groups)
SELECT s.permission, u.name AS person, g.name AS group_name FROM item_shares s
  LEFT JOIN users u ON u.id = s.user_id LEFT JOIN groups g ON g.id = s.group_id
 WHERE s.item_id = :item_id;

-- Users with their role and groups
SELECT u.email, r.name AS role, GROUP_CONCAT(g.name) AS groups  -- PostgreSQL: STRING_AGG(g.name, ',')
  FROM users u JOIN roles r ON r.id = u.role_id
  LEFT JOIN user_groups ug ON ug.user_id = u.id LEFT JOIN groups g ON g.id = ug.group_id
 GROUP BY u.id, u.email, r.name;

-- What each role may do
SELECT r.name, p.code FROM roles r JOIN role_permissions rp ON rp.role_id = r.id
  JOIN permissions p ON p.id = rp.permission_id ORDER BY r.name, p.code;

-- Pipeline rows as the API returns them
SELECT a.name, s.name AS sponsor, mo.name AS modality, t.name AS target, i.code AS indication, p.stage, p.activity
  FROM pipeline_programs p JOIN drug_assets a ON a.id = p.asset_id JOIN sponsors s ON s.id = a.sponsor_id
  JOIN modalities mo ON mo.id = a.modality_id JOIN targets t ON t.id = a.target_id
  JOIN indications i ON i.id = p.indication_id ORDER BY a.name, i.code;

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
          Paragraph("8.7 PostgreSQL setup", H3),
          code("""
psql -U postgres -c "CREATE ROLE gilead LOGIN PASSWORD '<password>';"
psql -U postgres -c "CREATE DATABASE gilead OWNER gilead;"
psql "postgresql://gilead:<password>@localhost:5432/gilead" -c "\\dt"     # list tables
psql ... -c "\\d+ runs"                                                   # describe a table
"""),
          Paragraph("8.8 Schema changes on PostgreSQL (Alembic)", H3),
          P(f"On PostgreSQL the API applies the migrations in {mono('migrations/versions/')} on start-up (one instance at a "
            "time). To change the schema: edit the models, create a migration, review it, then commit it with the code.", BODY),
          code("""
alembic revision --autogenerate -m "add folders table"   # write a migration from model changes; review the file
alembic upgrade head            # apply (also happens on API start-up)
alembic downgrade -1            # roll back one revision
alembic current                 # show the applied revision
alembic check                   # fails if the models changed without a migration
alembic upgrade head --sql      # print the SQL instead of running it (for DBA review)
"""),
          Paragraph("8.9 Backup and restore", H3),
          code("""
# SQLite
sqlite3 gilead.db ".backup 'gilead-$(date +%F).db'"
cp gilead-2026-11-20.db gilead.db                     # restore (stop the API first)

# PostgreSQL
pg_dump -Fc -d gilead -f gilead-$(date +%F).dump
pg_restore --clean --if-exists -d gilead gilead-2026-11-20.dump
"""),
          Paragraph("8.10 Moving from SQLite to PostgreSQL", H3),
          P(f"{mono('app/db/sqlite_to_postgres.py')} copies a SQLite database into PostgreSQL. It works on a temporary copy "
            "of the SQLite file (the original is never changed), brings it to the current schema, checks the data fits "
            "PostgreSQL's column limits, applies the migrations to the target, copies every table in one transaction, "
            "resets the id sequences and compares every row. The target must be empty unless --replace is given.", BODY),
          code("""
docker compose up -d db                                   # or use your own PostgreSQL server
python -m app.db.sqlite_to_postgres --target postgresql+psycopg://gilead:gilead@localhost:5432/gilead --check
python -m app.db.sqlite_to_postgres --target postgresql+psycopg://gilead:gilead@localhost:5432/gilead
# then in .env:  DATABASE_URL=postgresql+psycopg://gilead:gilead@localhost:5432/gilead
"""),
          P(f"Tables are copied in this order: {mono(', '.join(t.name for t in Base.metadata.sorted_tables))}.", BODY)]

# ------------------------------------------------------------------------------------------- DDL appendix
pg = postgresql.dialect()
ddl = []
for t in Base.metadata.sorted_tables:
    ddl.append(str(CreateTable(t).compile(dialect=pg)).strip() + ";")
    for i in sorted(t.indexes, key=lambda i: i.name):
        ddl.append(str(CreateIndex(i).compile(dialect=pg)).strip() + ";")
story += [PageBreak(), Paragraph("Appendix A · PostgreSQL DDL", H2),
          Paragraph("CREATE statements for every table and index, parent tables first, as created by the baseline "
                    "migration. JSON columns are JSONB on PostgreSQL.", INTRO),
          code("\n\n".join(ddl), DDL)]


def deco(c, d):
    c.saveState()
    c.setFillColor(RED)
    c.rect(0, A4[1] - 5, A4[0], 5, fill=1, stroke=0)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(MUT)
    c.drawString(18 * mm, 10 * mm, "Database ER Guide · Backend v0.4")
    c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {d.page}")
    c.restoreState()


SimpleDocTemplate(OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                  title="Database ER Guide", author="Backend team").build(story, onFirstPage=deco, onLaterPages=deco)
print("ok", OUT)
