"""ORM models. Schema changes go through Alembic (``alembic/versions``).

Rules: JSON columns are replaced, never mutated in place (use the typed accessors on
ProjectVersion); large documents are deferred (list queries never load them); state changes use
atomic statements (``aadhi.db.compare_and_set`` / ``atomic_add``).
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

if TYPE_CHECKING:  # pragma: no cover
    from .schemas.manifest import AssetManifest
    from .schemas.screenplay import Screenplay
    from .schemas.timeline import Timeline

JsonDoc = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# --- Users ------------------------------------------------------------------

ROLES = ("admin", "editor")  # students watch through share links; no viewer accounts


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="editor", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Bumped on logout, password/role/active changes: revokes every outstanding session token.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    daily_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = settings default
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    projects: Mapped[list["Project"]] = relationship(back_populates="owner")


# --- API keys saved in the Studio ---------------------------------------------

CREDENTIAL_PROVIDERS = ("gemini", "openai", "anthropic")
SERVER_CREDENTIAL_OWNER = "server"


class ApiCredential(TimestampMixin, Base):
    """An API key saved in the Studio (``aadhi.credentials``): an admin's server key (``owner_key``
    ``"server"``, used for everyone instead of the .env key) or a user's personal key (``"user:<id>"``,
    used only for the jobs that user starts). The key itself is only stored encrypted."""

    __tablename__ = "api_credentials"
    __table_args__ = (UniqueConstraint("owner_key", "provider", name="uq_api_credentials_owner_provider"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_key: Mapped[str] = mapped_column(String(32), nullable=False)  # "server" | "user:<id>"
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )  # None for the server key
    provider: Mapped[str] = mapped_column(String(16), nullable=False)  # gemini | openai | anthropic
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet token (aadhi.security.credentials_crypto)
    key_hint: Mapped[str] = mapped_column(String(32), nullable=False)  # "sk-ant-…WXYZ": vendor prefix + last 4
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)  # redacted result of the last test


# --- Projects & versions ----------------------------------------------------


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_owner_updated", "owner_id", "updated_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    subject_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    unit_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    session_number: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    session_title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="en-IN", nullable=False)
    # GenerationOptions (aadhi.pipeline.base) used for this project; legacy import ids etc.
    settings: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict, nullable=False)
    # Next ProjectVersion.number; allocate with aadhi.db.atomic_add(db, Project, id, "next_version_number", 1).
    next_version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.id", use_alter=True, ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped[User] = relationship(back_populates="projects")
    versions: Mapped[list["ProjectVersion"]] = relationship(
        back_populates="project",
        foreign_keys="ProjectVersion.project_id",
        order_by="ProjectVersion.number",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    current_version: Mapped["ProjectVersion | None"] = relationship(
        foreign_keys=[current_version_id], post_update=True, viewonly=True
    )
    sources: Mapped[list["SourceDocument"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class SourceDocument(TimestampMixin, Base):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)  # sanitised display name
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)  # private/ namespace
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Storage key (private/) of the IngestResult JSON once ingested.
    extracted_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    project: Mapped[Project] = relationship(back_populates="sources")


VERSION_STATUSES = ("draft", "generating", "awaiting_review", "building", "ready", "failed")


class ProjectVersion(TimestampMixin, Base):
    __tablename__ = "project_versions"
    __table_args__ = (UniqueConstraint("project_id", "number", name="uq_project_versions_project_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 2, 3 ... per project
    label: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="en-IN", nullable=False)
    # Translations: the version this one was translated from.
    source_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.id", ondelete="SET NULL"), nullable=True
    )
    # Large documents: deferred (load with sqlalchemy.orm.undefer / the typed accessors).
    screenplay: Mapped[dict[str, Any] | None] = mapped_column(JsonDoc, nullable=True, deferred=True)
    timeline: Mapped[dict[str, Any] | None] = mapped_column(JsonDoc, nullable=True, deferred=True)
    asset_manifest: Mapped[dict[str, Any] | None] = mapped_column(JsonDoc, nullable=True, deferred=True)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JsonDoc, default=list, nullable=False, deferred=True)
    # Models, prompt versions, durations, cost, plan (while awaiting review), scene history ...
    generation_meta: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict, nullable=False, deferred=True)
    # Cheap summaries for list endpoints.
    issue_counts: Mapped[dict[str, int]] = mapped_column(JsonDoc, default=dict, nullable=False)
    has_timeline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Optimistic concurrency: every screenplay change bumps ``revision`` (compare_and_set).
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # ``revision`` the current timeline/assets were built from (None = never built).
    built_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    project: Mapped[Project] = relationship(back_populates="versions", foreign_keys=[project_id])

    @property
    def timeline_stale(self) -> bool:
        return self.built_revision != self.revision

    # --- typed accessors (replace whole documents; never mutate in place) ----------------
    # Readers use ``validate_stored``: a document stored before NaN/Infinity and lone surrogates were
    # refused still loads (those values read as 0 / U+FFFD) instead of failing every job on the version.
    def get_screenplay(self) -> "Screenplay | None":
        from .schemas.jsonsafe import validate_stored
        from .schemas.screenplay import Screenplay

        return None if self.screenplay is None else validate_stored(Screenplay, self.screenplay)

    def set_screenplay(self, sp: "Screenplay | None") -> None:
        self.screenplay = None if sp is None else sp.model_dump(mode="json")

    def get_manifest(self) -> "AssetManifest | None":
        from .schemas.jsonsafe import validate_stored
        from .schemas.manifest import AssetManifest

        return None if self.asset_manifest is None else validate_stored(AssetManifest, self.asset_manifest)

    def set_manifest(self, m: "AssetManifest | None") -> None:
        self.asset_manifest = None if m is None else m.model_dump(mode="json")

    def get_timeline(self) -> "Timeline | None":
        from .schemas.jsonsafe import validate_stored
        from .schemas.timeline import Timeline

        return None if self.timeline is None else validate_stored(Timeline, self.timeline)

    def set_timeline(self, tl: "Timeline | None") -> None:
        self.timeline = None if tl is None else tl.model_dump(mode="json")
        self.has_timeline = tl is not None

    def set_issues(self, issues: list[Any]) -> None:
        dumped = [i.model_dump(mode="json") if hasattr(i, "model_dump") else dict(i) for i in issues]
        self.issues = dumped
        counts = {"error": 0, "warning": 0, "info": 0}
        for i in dumped:
            counts[i.get("severity", "warning")] = counts.get(i.get("severity", "warning"), 0) + 1
        self.issue_counts = counts


# --- Assets (content addressed) ----------------------------------------------

# Kinds served publicly from /media (capability URLs). Everything else lives under private/.
# ``snapshot``: the screenplay as Aadhi wrote it (``aadhi.changes``); never collected by the cleanup GC.
PUBLIC_ASSET_KINDS = ("upload", "figure", "tts", "scene_audio", "manim", "image", "video", "render", "captions", "poster")
PRIVATE_ASSET_KINDS = ("source", "extract", "screenshot", "intermediate", "snapshot")
ASSET_KINDS = PUBLIC_ASSET_KINDS + PRIVATE_ASSET_KINDS


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Content address: sha256 of the normalised generation inputs (or of the bytes for uploads).
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    # assets/<kind>/<key>/<random token>.<ext>  (public)  |  private/<kind>/<key>/<token>.<ext>
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AssetRef(Base):
    """Which project uses which asset: authorises asset keys in screenplays and enables GC."""

    __tablename__ = "asset_refs"
    __table_args__ = (UniqueConstraint("project_id", "asset_key", name="uq_asset_refs_project_asset"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    asset_key: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AssetClaim(Base):
    """A paid generation in flight (``aadhi.storage.assets``): the process producing asset ``key`` holds
    this row, so identical requests in other worker processes wait for its asset instead of paying
    again. Deleted when the producer ends; renewed (``expires_at``) while it runs. A claim is dead once
    it expired or its job no longer runs under ``worker_id`` + ``attempt`` (the job lease)."""

    __tablename__ = "asset_claims"
    __table_args__ = (Index("ix_asset_claims_expires_at", "expires_at"),)

    key: Mapped[str] = mapped_column(String(128), primary_key=True)  # Asset.key being generated
    holder: Mapped[str] = mapped_column(String(64), nullable=False)  # random per claim: renew/release match it
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # holding job (None: not run by a worker)
    worker_id: Mapped[str | None] = mapped_column(String(160), nullable=True)  # Job.locked_by of the holder
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Job.attempts (lease fencing token)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The paid provider job the holder started for this asset (``providers.operations.OperationRecord`` JSON: status,
    # operation name, key fingerprint; never a secret). Kept when another job takes the claim over, so the taker
    # polls the same operation instead of paying again (migration 0004).
    operation: Mapped[dict[str, Any] | None] = mapped_column(JsonDoc, nullable=True)


# --- Media library (per user) -------------------------------------------------

LIBRARY_ITEM_KINDS = ("image", "video")
LIBRARY_ITEM_SOURCES = ("upload", "generated", "figure")


class LibraryItem(TimestampMixin, Base):
    """A picture or clip in one user's own media library (``aadhi.library``, migration 0005).

    The title, description and keywords are this user's words. The media is the shared content-addressed
    ``Asset`` row (``asset_key``, deliberately no foreign key): identical bytes or generation inputs are one
    row for everybody, so ownership never comes from the asset row. ``search_text`` holds the item's
    normalised words and stems (``library.search_text``) for search and the matcher's prefilter.
    """

    __tablename__ = "library_items"
    __table_args__ = (
        UniqueConstraint("user_id", "asset_key", name="uq_library_items_user_asset"),
        Index("ix_library_items_user_kind", "user_id", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    asset_key: Mapped[str] = mapped_column(String(128), index=True, nullable=False)  # -> assets.key
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # LIBRARY_ITEM_KINDS
    title: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JsonDoc, default=list, nullable=False)  # <= 20 x <= 40 chars
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # LIBRARY_ITEM_SOURCES
    origin_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    origin_scene_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt: Mapped[str | None] = mapped_column(String(1200), nullable=True)  # generated media only
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    search_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Jobs ---------------------------------------------------------------------

JOB_KINDS = (
    "generate_lecture",  # ingest -> plan [-> review] -> script -> validate -> companion -> assets -> timeline
    "regenerate_scene",
    "build_assets",  # (re)build assets + timeline for an edited version
    "render_video",  # timeline -> MP4 + captions + chapters
    "translate",  # fills a new version with narration in another language
    "import_legacy",
    "cleanup",  # retention: old job events, analytics, orphaned assets
)
VERSION_MUTATING_KINDS = ("generate_lecture", "regenerate_scene", "build_assets", "translate")
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled", "awaiting_review")
ACTIVE_JOB_STATUSES = ("queued", "running", "awaiting_review")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "kind", "priority", "run_after"),
        Index("ix_jobs_project_created", "project_id", "created_at"),
        # At most one active version-mutating job per version (API maps IntegrityError -> 409 job_in_progress).
        Index(
            "uq_jobs_active_version",
            "version_id",
            unique=True,
            sqlite_where=text(
                "status IN ('queued','running','awaiting_review') AND kind IN "
                "('generate_lecture','regenerate_scene','build_assets','translate')"
            ),
            postgresql_where=text(
                "status IN ('queued','running','awaiting_review') AND kind IN "
                "('generate_lecture','regenerate_scene','build_assets','translate')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.id", ondelete="SET NULL"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # 0..1
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict, nullable=False, deferred=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonDoc, nullable=True, deferred=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # redacted, user-facing
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)  # budget, cancelled, lease_lost ...
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # also the lease fencing token
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)  # set by aadhi.jobs.queue from settings
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)  # lower runs first
    run_after: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(160), nullable=True)  # host:pid:boot_uuid:thread
    locked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_id_id", "job_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="info", nullable=False)  # info|warning|error|progress
    stage: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict, nullable=False)


# --- Renders ------------------------------------------------------------------


class Render(Base):
    __tablename__ = "renders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("project_versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="en-IN", nullable=False)
    built_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)  # screenplay revision rendered
    video_asset_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    srt_asset_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vtt_asset_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chapters_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    options: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- Visual Review ------------------------------------------------------------

VISUAL_REVIEW_STATES = ("pending", "approved", "changed", "removed")


class VisualReview(Base):
    """A teacher's sign-off on one scene's visual (``aadhi.review``). Advisory and kept outside the screenplay,
    so approving never changes a scene hash or a build cache. ``fingerprint`` is what the scene asked its visual
    to show when the state was set; when the scene's visual request changes, an approval reads as pending again
    (migration 0006)."""

    __tablename__ = "visual_reviews"
    __table_args__ = (UniqueConstraint("version_id", "scene_id", name="uq_visual_reviews_version_scene"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False
    )
    scene_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)  # VISUAL_REVIEW_STATES
    fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# --- Usage / cost -------------------------------------------------------------


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_user_time", "user_id", "created_at"),
        Index("ix_usage_project_time", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)  # llm|vision|tts|image|video|gif
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    characters: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # "server" | "user" (paid with the user's personal API key: not counted against the daily budget)
    billed_to: Mapped[str] = mapped_column(String(8), default="server", server_default="server", nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- Sharing & learner analytics ---------------------------------------------


class ShareLink(Base):
    __tablename__ = "share_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=True
    )  # None = project's current version
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


ANALYTICS_EVENTS = (
    "session_start",
    "scene_enter",
    "scene_complete",
    "quiz_answer",
    "pause",
    "resume",
    "seek",
    "complete",
)


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"
    __table_args__ = (
        Index("ix_analytics_project_event", "project_id", "event"),
        Index("ix_analytics_project_time", "project_id", "created_at"),
        Index("ix_analytics_share_time", "share_link_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_versions.id", ondelete="SET NULL"), nullable=True
    )
    share_link_id: Mapped[int | None] = mapped_column(ForeignKey("share_links.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    viewer_id: Mapped[str] = mapped_column(String(64), nullable=False)  # random per browser session
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    scene_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JsonDoc, default=dict, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
