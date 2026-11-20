"""Idempotent demo seed. Mirrors the prototype's illustrative data. Disable with SEED_DEMO_DATA=false."""
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.security import hash_password
from app.db import models as m
from app.seed_connectors import CONNECTORS
from app.services.catalog import WORKFLOWS

LICENSED = {"Elsevier ScienceDirect", "Wiley Online Library", "Citeline Trialtrove", "Citeline Pharmaprojects", "IQVIA MIDAS",
            "Evaluate Pharma", "MMIT", "Fingertip Formulary", "Komodo Health", "Optum", "MarketScan", "TriNetX", "Derwent Innovation"}
DISABLED = {"Wiley Online Library", "Derwent Innovation", "Optum"}

ASSETS = [  # name, sponsor, modality, target, indication, stage, activity, geography
    ("Asset A", "Sponsor 1", "Small molecule", "IRAK4 inhibitor", "SLE", "Phase 2", "Recruiting", "US"),
    ("Asset A", "Sponsor 1", "Small molecule", "IRAK4 inhibitor", "RA", "Phase 1", "Active", "US"),
    ("Asset B", "Sponsor 2", "Targeted protein degrader", "IRAK4 degrader", "AD", "Phase 2", "Active", "Global"),
    ("Asset B", "Sponsor 2", "Targeted protein degrader", "IRAK4 degrader", "SLE", "Phase 1", "Recruiting", "Global"),
    ("Asset C", "Sponsor 3", "Small molecule", "IRAK4 inhibitor", "RA", "Phase 2", "Completed", "EU"),
    ("Asset D", "Sponsor 4", "Small molecule", "IRAK4 inhibitor", "AD", "Preclinical", "—", "US"),
    ("Asset E", "Sponsor 5", "Targeted protein degrader", "IRAK4 degrader", "RA", "Preclinical", "—", "US"),
    ("Asset F", "Sponsor 6", "Small molecule", "IRAK4/IRAK1 dual inhibitor", "SLE", "Discovery", "—", "US"),
    ("Asset G", "Sponsor 7", "Small molecule", "IRAK4 inhibitor", "AD", "Phase 1", "Terminated", "EU"),
    ("Asset H", "Sponsor 8", "Monoclonal antibody", "IL-1R pathway antibody", "RA", "Phase 3", "Active", "Global"),
    ("Asset I", "Sponsor 9", "Monoclonal antibody", "IL-1R pathway antibody", "SLE", "Phase 2", "Recruiting", "US"),
    ("Asset J", "Sponsor 3", "Small molecule", "IRAK4 inhibitor", "AD", "Phase 1", "Recruiting", "US"),
]

CRITERIA = [  # key, name, rule, scale 1/3/5 definitions
    ("population", "Addressable Patient Population", "1-5 based on addressable patient population thresholds",
     ("< 50 K patients", "200 - 349 K patients", "> 500 K patients")),
    ("unmet_need", "Unmet Need", "1-5 based on disease burden and treatment gaps",
     ("Low morbidity and limited QoL impact", "Moderate morbidity with significant QoL impact",
      "High morbidity and QoL impact, increased long-term mortality")),
    ("headroom", "Competitive Headroom", "1-5 based on marketed competitors and Phase 2+ pipeline assets",
     ("> 30 marketed compounds and > 30 P2+ assets", "10 - 19 marketed compounds and 10 - 19 P2+ assets",
      "< 5 marketed compounds and < 5 P2+ assets")),
    ("payer", "Payer Management", "1-5 based on coverage restrictions and reimbursement barriers",
     ("Frequent coverage restrictions (e.g., multiple steps)", "Moderate restrictions (e.g., burdensome PAs)",
      "Limited restrictions (e.g., PA to label, preferred tier)")),
    ("pricing", "Pricing Potential", "1-5 based on annual WAC benchmarks for comparable therapies",
     ("< $10 K annual WAC", "$50 - 99 K annual WAC", "> $150 K annual WAC")),
]

PROMPTS = [  # name, therapeutic area > indication, variables, visibility, status
    ("Identify top 5 commercial opportunities for IRAK4", "Inflammation › SLE, RA, AD", "{target}, {indication}", "All users", "Published"),
    ("Show market size and forecast for atopic dermatitis", "Inflammation › AD", "{indication}, {geography}, {timeframe}", "All users", "Published"),
    ("Compare payer access across top 3 indications", "Inflammation › SLE, RA, AD", "{target}, {geography}", "Commercial", "Draft"),
    ("Which indications have high unmet need?", "Inflammation › SLE, RA, AD", "{target}", "All users", "Published"),
]
# Competitive Insights workflows (admin "AI Workflows") and their report templates
WORKFLOW_ADMIN = {"competitive": ("Commercial", "Maria Chen", "v4", "Published"), "endpoint": ("Clinical", "Sam Patel", "v2", "Published"),
                  "epidemiology": ("Market", "Maria Chen", "v3", "Draft"), "landscape": ("Landscape", "Alex Johnson", "v1", "Published"),
                  "unmet_need": ("Clinical", "Sam Patel", "v1", "Draft")}
TEMPLATES = {"competitive": ("Competitive Opportunity Report", "v3", "Published"),
             "endpoint": ("Clinical Endpoint Assessment", "v2", "Published"), "epidemiology": ("Epidemiology Report", "v1", "Draft"),
             "landscape": ("Therapeutic Landscape Assessment", "v1", "Published"),
             "unmet_need": ("Unmet Medical Needs Assessment", "v1", "Draft")}


def seed(db) -> None:
    if db.scalar(select(m.User).limit(1)):
        return
    pw = hash_password("ChangeMe123!")  # demo only
    db.add_all([
        m.User(name="Maria Chen", email="maria.chen@example.com", role="Admin", groups=["R&D"], password_hash=pw),
        m.User(name="Swanik", email="swanik@example.com", role="Analyst", groups=["Commercial", "Clinical"], password_hash=pw),
        m.User(name="Sam Patel", email="sam.patel@example.com", role="Reviewer", groups=["Market Access"], password_hash=pw),
        m.User(name="Jordan Lee", email="jordan.lee@example.com", role="Viewer", groups=["Clinical"], password_hash=pw, status="Inactive"),
    ])
    for cat, name, desc in CONNECTORS:
        db.add(m.Connector(category=cat, name=name, description=desc, enabled=name not in DISABLED,
                           license="Licensed" if name in LICENSED else "Open",
                           health="Warning" if name == "Gilead internal clinical repositories" else "Failed" if name == "FDA FAERS" else "Healthy",
                           last_sync=datetime.now(timezone.utc)))
    for a in ASSETS:
        db.add(m.PipelineAsset(name=a[0], sponsor=a[1], modality=a[2], target=a[3], indication=a[4], stage=a[5],
                               activity=a[6], geography=a[7]))
    db.add(m.ScoringModel(name="Commercial Indication Prioritization", version="v2.1", status="Published", criteria=[
        {"key": k, "name": n, "rule": r, "scale": {"1": s[0], "3": s[1], "5": s[2]}, "weight": 20} for k, n, r, s in CRITERIA]))
    db.commit()


def _upsert_record(db, kind: str, name: str, status: str, version: str, data: dict) -> None:
    """Insert, or fill in data keys a row created by an older seed is missing. Never overwrites admin edits."""
    r = db.scalar(select(m.AdminRecord).where(m.AdminRecord.kind == kind, m.AdminRecord.name == name))
    if r is None:
        db.add(m.AdminRecord(kind=kind, name=name, status=status, version=version, data=data))
    elif any(k not in (r.data or {}) for k in data):
        r.data = {**data, **(r.data or {})}


def seed_reference(db) -> None:
    """Idempotent reference data for the current UI: workflows, report templates and prompts. Safe on every start."""
    for w in WORKFLOWS:
        cat, owner, ver, status = WORKFLOW_ADMIN[w["key"]]
        _upsert_record(db, "workflow", w["name"], status, ver,
                       {"key": w["key"], "category": cat, "owner": owner, "subcategories": len(w["subcategories"])})
        name, tver, tstatus = TEMPLATES[w["key"]]
        _upsert_record(db, "template", name, tstatus, tver, {"linked_workflow": w["name"], "workflow_key": w["key"],
                                                             "sections": len(w["sections"])})
    renames = {"modality": {"Protein degrader": "Targeted protein degrader", "Biologic": "Monoclonal antibody"},
               "stage": {"Research": "Discovery"}}  # demo values used by the previous UI
    for a in db.scalars(select(m.PipelineAsset)):
        for col, mp in renames.items():
            if getattr(a, col) in mp:
                setattr(a, col, mp[getattr(a, col)])
    for name, scope, variables, visibility, status in PROMPTS:
        _upsert_record(db, "prompt", name, status, "v1",
                       {"scope": scope, "variables": variables, "visibility": visibility})
    db.commit()
