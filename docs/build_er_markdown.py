"""Regenerates ER_DIAGRAM.md (Mermaid) from the SQLAlchemy models, so it always matches app/db/models.py.

Usage (from the repo root):  PYTHONPATH=. python docs/build_er_markdown.py
"""
from pathlib import Path

import app.db.models  # noqa: F401  (registers every table)
from app.db.base import Base

OUT = Path(__file__).resolve().parent.parent / "ER_DIAGRAM.md"

GROUPS = [  # (title, tables) - one Mermaid diagram per domain keeps each readable
    ("Identity & access", ["roles", "permissions", "role_permissions", "users", "groups", "user_groups",
                           "revoked_tokens", "notifications", "audit_log"]),
    ("Research runs & workspace", ["users", "groups", "runs", "workspace_items", "item_versions", "item_shares", "item_notes"]),
    ("Prioritization scoring", ["users", "scoring_models", "assessments", "assessment_overrides", "scenarios"]),
    ("Pipeline catalogue", ["sponsors", "modalities", "targets", "indications", "drug_assets", "pipeline_programs"]),
    ("Configuration (no foreign keys)", ["connectors", "admin_records"]),
]
LABEL = {  # child.fk -> verb shown on the relationship line
    "users.role_id": "has_role", "role_permissions.role_id": "grants", "role_permissions.permission_id": "granted_by",
    "user_groups.user_id": "member_of", "user_groups.group_id": "has_member", "runs.user_id": "owns",
    "runs.parent_run_id": "follow_up_of", "workspace_items.owner_id": "owns", "workspace_items.run_id": "saved_from",
    "item_versions.item_id": "has", "item_shares.item_id": "has", "item_shares.user_id": "shared_with",
    "item_shares.group_id": "shared_with", "item_notes.item_id": "has", "item_notes.user_id": "writes",
    "assessments.owner_id": "owns", "assessments.model_id": "template_of", "assessment_overrides.assessment_id": "has",
    "assessment_overrides.user_id": "overrides", "scenarios.assessment_id": "has", "notifications.user_id": "receives",
    "audit_log.user_id": "performs", "revoked_tokens.user_id": "signed_out", "drug_assets.sponsor_id": "develops",
    "drug_assets.modality_id": "modality_of", "drug_assets.target_id": "target_of", "pipeline_programs.asset_id": "developed_in",
    "pipeline_programs.indication_id": "studied_in",
}


def mtype(col) -> str:
    t = str(col.type).split("(")[0].lower()
    return {"varchar": "varchar", "integer": "int", "datetime": "datetime", "boolean": "boolean",
            "json": "json", "text": "text"}.get(t, t)


def entity(t) -> list[str]:
    lines = [f"    {t.name.upper()} {{"]
    for c in t.columns:
        keys = []
        if c.primary_key:
            keys.append("PK")
        if c.foreign_keys:
            keys.append("FK")
        if c.unique:
            keys.append("UK")
        note = ' "nullable"' if c.nullable and not c.primary_key else ""
        lines.append(f"        {mtype(c)} {c.name}{' ' + ','.join(keys) if keys else ''}{note}")
    lines.append("    }")
    return lines


def rels(tables: set[str]) -> list[str]:
    out = []
    for t in Base.metadata.sorted_tables:
        if t.name not in tables:
            continue
        for c in t.columns:
            for fk in c.foreign_keys:
                parent = fk.column.table.name
                if parent not in tables:
                    continue
                left = "o|" if c.nullable else "||"
                out.append(f"    {parent.upper()} {left}--o{{ {t.name.upper()} : {LABEL.get(f'{t.name}.{c.name}', c.name)}")
    return out


T = {t.name: t for t in Base.metadata.sorted_tables}
missing = set(T) - {n for _, names in GROUPS for n in names}
assert not missing, f"tables not placed in a diagram: {missing}"

md = ["# Database ER Diagram", "",
      "Entity-relationship diagrams for the platform database, grouped by domain.",
      f"{len(T)} tables. Keys: PK primary key, FK foreign key, UK unique. `||--o{{` = one-to-many (required), "
      "`o|--o{` = optional parent.", "",
      "A full guide with descriptions, JSON shapes and database commands is in "
      "[`docs/Database_ER_Guide.pdf`](./docs/Database_ER_Guide.pdf).", ""]
for title, names in GROUPS:
    md += [f"## {title}", "", "```mermaid", "erDiagram"]
    for n in names:
        md += entity(T[n])
    md += [""] + rels(set(names)) + ["```", ""]
md += ["## Notes", "",
       "- `users` and `groups` appear in several diagrams because many domains reference them.",
       "- `runs.thread_id` groups the turns of a conversation (the first turn's id). It is a logical link without a "
       "foreign-key constraint.",
       "- `role_permissions` and `user_groups` are many-to-many link tables with composite primary keys.",
       "- An `item_shares` row targets either a user (`user_id`) or a group (`group_id`), never both.",
       "- `assessment_overrides.indication` / `criterion_key` refer to values inside the assessment's own "
       "`indications` / `criteria` JSON (the assessment is a working copy of a scoring model).",
       "- JSON columns hold snapshots and flexible payloads (run params/results, saved snapshots, scenario snapshots, "
       "criteria templates, user context and preferences). IDs inside JSON are not enforced by the database.", ""]
OUT.write_text("\n".join(md))
print("ok", OUT)
