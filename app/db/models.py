"""Relational model. JSON columns hold flexible, module-specific payloads and snapshots (run results, saved
results, scenario snapshots, criteria templates); anything that is shared, referenced or filtered on is a real
column or its own table (roles, permissions, groups, pipeline lookups, scoring overrides)."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


def default_context() -> dict:
    return {"target": "IRAK4", "mechanism": "IRAK4 inhibition", "indications": ["SLE", "RA", "AD"],
            "geography": "United States", "session": "IRAK4 Inflammation Programme"}


def default_prefs() -> dict:
    return {"deep_research": False, "in_app_notifications": True, "email": False}


# ------------------------------------------------------------------ identity & access
class Permission(Base):
    """An action a role may perform: view, create, edit, execute, publish, share, administer."""
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    description: Mapped[str] = mapped_column(String(200), default="")


class RolePermission(Base):
    """Many-to-many: which permissions each role grants."""
    __tablename__ = "role_permissions"
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id"), primary_key=True)


class Role(Base):
    """Admin | Analyst | Reviewer | Viewer."""
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20), unique=True)
    description: Mapped[str] = mapped_column(String(200), default="")
    permissions: Mapped[list["Permission"]] = relationship(secondary="role_permissions", lazy="selectin",
                                                           order_by="Permission.code")


class Group(Base):
    """Team used for group sharing (e.g. Commercial, Clinical, R&D, Market Access)."""
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(200), default="")


class UserGroup(Base):
    """Many-to-many: group membership."""
    __tablename__ = "user_groups"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), primary_key=True, index=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300), default="")
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="Active")
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    context: Mapped[dict] = mapped_column(JSON, default=default_context)  # header "Research context" chip
    prefs: Mapped[dict] = mapped_column(JSON, default=default_prefs)      # Preferences dialog
    role: Mapped["Role"] = relationship(lazy="joined")
    groups: Mapped[list["Group"]] = relationship(secondary="user_groups", lazy="selectin", order_by="Group.name")

    @property
    def permission_codes(self) -> set[str]:
        return {p.code for p in self.role.permissions} if self.role else set()

    @property
    def group_names(self) -> list[str]:
        return [g.name for g in self.groups]


class Run(Base):
    """One execution of a research workflow (AI search, pipeline build, CIOA report).
    Backs Home 'In Progress', History > Research activity, the loaders, and Resume."""
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    module: Mapped[str] = mapped_column(String(20), index=True)  # ai|pipeline|cioa (cioa = Competitive Insights)
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
    # Conversation the run belongs to (AI Search / Pipeline / Competitive Insights threads). = id of the first turn.
    thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    notify: Mapped[bool] = mapped_column(Boolean, default=False)       # "Notify me when ready"
    workflow: Mapped[str] = mapped_column(String(40), default="")     # Competitive Insights workflow key
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
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)    # share with a person ...
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), nullable=True)  # ... or with a group
    permission: Mapped[str] = mapped_column(String(10), default="view")  # view|edit
    group: Mapped["Group | None"] = relationship(lazy="joined")


class ItemNote(Base):
    __tablename__ = "item_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("workspace_items.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ------------------------------------------------------------------ pipeline catalogue
class Sponsor(Base):
    __tablename__ = "sponsors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class Modality(Base):
    __tablename__ = "modalities"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)


class Target(Base):
    """Molecular target / mechanism, e.g. 'IRAK4 inhibitor'."""
    __tablename__ = "targets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class Indication(Base):
    __tablename__ = "indications"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)   # short label used across the app, e.g. "SLE"
    name: Mapped[str] = mapped_column(String(120), default="")   # e.g. "Systemic Lupus Erythematosus"


class DrugAsset(Base):
    """A drug / program asset, independent of indication (e.g. 'Asset A' from 'Sponsor 1')."""
    __tablename__ = "drug_assets"
    __table_args__ = (UniqueConstraint("name", "sponsor_id", name="uq_drug_assets_name_sponsor"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    sponsor_id: Mapped[int] = mapped_column(ForeignKey("sponsors.id"), index=True)
    modality_id: Mapped[int] = mapped_column(ForeignKey("modalities.id"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    sponsor: Mapped["Sponsor"] = relationship(lazy="joined")
    modality: Mapped["Modality"] = relationship(lazy="joined")
    target: Mapped["Target"] = relationship(lazy="joined")


class PipelineProgram(Base):
    """One asset being developed in one indication (and geography): stage and trial activity live here."""
    __tablename__ = "pipeline_programs"
    __table_args__ = (UniqueConstraint("asset_id", "indication_id", "geography", name="uq_pipeline_programs_asset_ind_geo"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("drug_assets.id"), index=True)
    indication_id: Mapped[int] = mapped_column(ForeignKey("indications.id"), index=True)
    stage: Mapped[str] = mapped_column(String(30), index=True)
    activity: Mapped[str] = mapped_column(String(30), default="—")  # Recruiting|Active|Completed|Terminated|—
    geography: Mapped[str] = mapped_column(String(30), default="US")
    source: Mapped[str] = mapped_column(String(60), default="Citeline Pharmaprojects")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    asset: Mapped["DrugAsset"] = relationship(lazy="joined")
    indication: Mapped["Indication"] = relationship(lazy="joined")

    def as_row(self) -> dict:
        """Flat shape used by the pipeline views and the API (same keys as the former pipeline_assets table)."""
        a = self.asset
        return {"id": self.id, "asset_id": a.id, "name": a.name, "sponsor": a.sponsor.name, "modality": a.modality.name,
                "target": a.target.name, "indication": self.indication.code, "stage": self.stage,
                "activity": self.activity, "geography": self.geography, "source": self.source}


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
    status: Mapped[str] = mapped_column(String(20), default="Draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    scenarios: Mapped[list["Scenario"]] = relationship(cascade="all, delete-orphan")
    override_rows: Mapped[list["AssessmentOverride"]] = relationship(cascade="all, delete-orphan", lazy="selectin",
                                                                     order_by="AssessmentOverride.id")

    @property
    def overrides(self) -> dict:
        """{indication: {criterion_key: {score, justification, user_id, at}}} - the shape the scoring engine uses."""
        out: dict = {}
        for o in self.override_rows:
            out.setdefault(o.indication, {})[o.criterion_key] = {
                "score": o.score, "justification": o.justification, "user_id": o.user_id,
                "at": o.created_at.isoformat() if o.created_at else None}
        return out


class AssessmentOverride(Base):
    """A human override of an AI-recommended score. Justification is required; who and when are traceable."""
    __tablename__ = "assessment_overrides"
    __table_args__ = (UniqueConstraint("assessment_id", "indication", "criterion_key", name="uq_assessment_overrides_cell"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    indication: Mapped[str] = mapped_column(String(60))     # one of assessments.indications
    criterion_key: Mapped[str] = mapped_column(String(60))  # one of assessments.criteria[].key
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # null = marked as Missing
    justification: Mapped[str] = mapped_column(Text)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    weights: Mapped[dict] = mapped_column(JSON)
    overrides: Mapped[dict] = mapped_column(JSON, default=dict)  # snapshot of the overrides at save time
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


class RevokedToken(Base):
    """Tokens ended by POST /auth/logout. Rows can be purged once expires_at has passed."""
    __tablename__ = "revoked_tokens"
    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
