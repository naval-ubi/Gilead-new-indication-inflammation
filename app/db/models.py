"""Relational model. JSON columns hold flexible, module-specific payloads
(run results, scoring overrides); everything you filter/sort on is a real column."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


def default_context() -> dict:
    return {"target": "IRAK4", "mechanism": "IRAK4 inhibition", "indications": ["SLE", "RA", "AD"],
            "geography": "United States", "session": "IRAK4 Inflammation Programme"}


def default_prefs() -> dict:
    return {"deep_research": False, "in_app_notifications": True, "email": False}


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300), default="")
    role: Mapped[str] = mapped_column(String(20), default="Analyst")  # Admin|Analyst|Reviewer|Viewer
    groups: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="Active")
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    context: Mapped[dict] = mapped_column(JSON, default=default_context)  # header "Research context" chip
    prefs: Mapped[dict] = mapped_column(JSON, default=default_prefs)      # Preferences dialog


class Run(Base):
    """One execution of a research workflow (AI search, pipeline build, CIOA report).
    Backs Home 'In Progress', History > Research activity, the loaders, and Resume."""
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    module: Mapped[str] = mapped_column(String(20), index=True)  # ai|pipeline|cioa
    kind: Mapped[str] = mapped_column(String(30), default="Search")  # Search|Workflow run|Report generated
    title: Mapped[str] = mapped_column(String(300))
    question: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(20), default="Standard")  # Standard|Deep Research
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="In Progress", index=True)  # In Progress|Paused|Failed|Completed|Cancelled
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{name,status}]
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    step_label: Mapped[str] = mapped_column(String(300), default="")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    project: Mapped[str] = mapped_column(String(120), default="")
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # "Clear history" is a soft delete


class WorkspaceItem(Base):
    __tablename__ = "workspace_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    item_type: Mapped[str] = mapped_column(String(30))  # Search|Question set|Report|Assessment|Pipeline view
    module: Mapped[str] = mapped_column(String(20))
    folder: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="Saved")  # Saved|Draft|Completed
    question: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # saved result; reopening does NOT rerun
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, index=True)
    versions: Mapped[list["ItemVersion"]] = relationship(cascade="all, delete-orphan", order_by="ItemVersion.id.desc()")
    shares: Mapped[list["ItemShare"]] = relationship(cascade="all, delete-orphan")
    notes: Mapped[list["ItemNote"]] = relationship(cascade="all, delete-orphan", order_by="ItemNote.id")


class ItemVersion(Base):
    __tablename__ = "item_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("workspace_items.id"), index=True)
    label: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(String(300), default="")
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ItemShare(Base):
    __tablename__ = "item_shares"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("workspace_items.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    group: Mapped[str | None] = mapped_column(String(80), nullable=True)
    permission: Mapped[str] = mapped_column(String(10), default="view")  # view|edit


class ItemNote(Base):
    __tablename__ = "item_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("workspace_items.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PipelineAsset(Base):
    __tablename__ = "pipeline_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    sponsor: Mapped[str] = mapped_column(String(120), index=True)
    modality: Mapped[str] = mapped_column(String(60), index=True)
    target: Mapped[str] = mapped_column(String(120), index=True)  # e.g. "IRAK4 inhibitor"
    indication: Mapped[str] = mapped_column(String(60), index=True)
    stage: Mapped[str] = mapped_column(String(30), index=True)
    activity: Mapped[str] = mapped_column(String(30), default="—")  # Recruiting|Active|Completed|Terminated|—
    geography: Mapped[str] = mapped_column(String(30), default="US")
    source: Mapped[str] = mapped_column(String(60), default="Citeline Pharmaprojects")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ScoringModel(Base):
    """Admin-managed template: criteria, scale definitions, default weights."""
    __tablename__ = "scoring_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(20), default="v1")
    status: Mapped[str] = mapped_column(String(20), default="Draft")  # Draft|Published|Inactive
    criteria: Mapped[list] = mapped_column(JSON, default=list)  # [{key,name,rule,scale:{1,3,5},weight}]


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("scoring_models.id"))
    title: Mapped[str] = mapped_column(String(200))
    target: Mapped[str] = mapped_column(String(80))
    indications: Mapped[list] = mapped_column(JSON, default=list)
    criteria: Mapped[list] = mapped_column(JSON, default=list)   # working copy (users can add/edit/remove)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)    # {criterion_key: percent}
    ai_scores: Mapped[dict] = mapped_column(JSON, default=dict)  # {indication: {criterion_key: 1..5 | null}}
    overrides: Mapped[dict] = mapped_column(JSON, default=dict)  # {indication: {key: {score,justification,user_id,at}}}
    status: Mapped[str] = mapped_column(String(20), default="Draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    scenarios: Mapped[list["Scenario"]] = relationship(cascade="all, delete-orphan")


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    weights: Mapped[dict] = mapped_column(JSON)
    overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    ranking: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Connector(Base):
    __tablename__ = "connectors"
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[int] = mapped_column(Integer)  # index into CATEGORIES
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    health: Mapped[str] = mapped_column(String(20), default="Healthy")  # Healthy|Warning|Failed
    license: Mapped[str] = mapped_column(String(20), default="Open")    # Open|Licensed
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)            # non-secret config only
    secret_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)  # pointer into a vault


class AdminRecord(Base):
    """Generic config rows the Admin screen manages: workflows, report templates, prompts."""
    __tablename__ = "admin_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # workflow|template|prompt
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="Draft")
    version: Mapped[str] = mapped_column(String(20), default="v1")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(500))
    route: Mapped[str] = mapped_column(String(40), default="home")
    unread: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(300))
    entity: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[str] = mapped_column(String(40), default="")
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
