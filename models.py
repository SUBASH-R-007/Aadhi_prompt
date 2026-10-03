from sqlalchemy import Column, Integer, BigInteger, Float, Boolean, String, Text, ForeignKey, DateTime, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base
import datetime

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)

    projects = relationship("Project", back_populates="owner")

class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    subject_name = Column(String(255), nullable=False, index=True)
    unit_name = Column(String(255), nullable=True)
    session_number = Column(String(50), nullable=True)
    session_title = Column(String(255), nullable=True)
    json_data = Column(Text, nullable=False) # Stores the concept_map and scenes payload
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    owner = relationship("User", back_populates="projects")

class VideoExport(Base):
    """One attempt at recording a lesson to a video file (see exports.py).

    A retry is a new row; failed and completed attempts are kept."""
    __tablename__ = "video_exports"

    id = Column(String(32), primary_key=True)  # random uuid4 hex
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    source = Column(String(20), nullable=False, default="lesson")  # lesson | manual
    status = Column(String(20), nullable=False, default="QUEUED", index=True)
    stage = Column(String(255), nullable=True)  # human-readable detail of the current status
    progress = Column(Float, nullable=True)  # 0..1 within the current status; null when unknown
    format = Column(String(10), nullable=True)  # container: webm | mp4
    mime_type = Column(String(100), nullable=True)
    file_name = Column(String(255), nullable=True)  # download name offered to the user
    storage_key = Column(String(512), nullable=True)  # path inside EXPORTS_DIR; never sent to clients
    file_size = Column(BigInteger, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    has_audio = Column(Boolean, nullable=True)  # has an audible sound track (silent tracks count as False)
    error_message = Column(Text, nullable=True)
    retry_of_id = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

class Asset(Base):
    """A reusable media file in the asset library (see assets.py).

    System assets (Aadhi's clips, the intro media) have no owner and are readable by every
    signed-in user; private assets belong to one user. Several rows may point at the same
    physical file (same content for different owners) so nothing is stored twice."""
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("scope_key", "storage_volume", "storage_key", name="uq_asset_location"),)

    id = Column(String(32), primary_key=True)  # random uuid4 hex; the stable asset ID lessons refer to
    scope_key = Column(String(40), nullable=False, index=True)  # "system" or "user:<id>"
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # NULL for system assets
    kind = Column(String(10), nullable=False, index=True)  # video | audio | image
    source = Column(String(20), nullable=False, index=True)  # mascot | narration | manim | ai-video | ai-image | upload | system | export
    status = Column(String(10), nullable=False, default="ready", index=True)  # pending | ready | failed | deleted
    file_name = Column(String(255), nullable=False)  # display name only, never used as a path
    mime_type = Column(String(100), nullable=False)  # from the file's own bytes
    storage_volume = Column(String(20), nullable=False)  # storage root: assets | static | system
    storage_key = Column(String(512), nullable=False)  # server-generated key inside that root
    file_size = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=True, index=True)
    duration_seconds = Column(Float, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    has_audio = Column(Boolean, nullable=True)  # videos only
    details = Column(Text, nullable=True)  # JSON: source-specific facts (voice, engine, scene, prompt…)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)

class AssetReference(Base):
    """Where an asset is used: one row per asset, saved lesson (project) and place in the lesson."""
    __tablename__ = "asset_references"

    id = Column(Integer, primary_key=True)
    asset_id = Column(String(32), ForeignKey("assets.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    field = Column(String(160), nullable=False)  # e.g. "scenes[3].video_asset_id"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class AIGeneration(Base):
    """AI media cache entry (see ai_cache.py): the canonical identity of an AI generation request and
    the library asset it produced. The asset itself is the cached media; nothing is copied."""
    __tablename__ = "ai_generations"
    __table_args__ = (Index("ix_ai_generation_lookup", "scope_key", "generation_hash"),)

    id = Column(Integer, primary_key=True)
    generation_hash = Column(String(64), nullable=False)  # SHA-256 of the canonical identity (not the file's content hash)
    scope_key = Column(String(40), nullable=False)  # "user:<id>" (private to that user) or "system" (shared)
    asset_id = Column(String(32), ForeignKey("assets.id"), nullable=False, index=True)
    media_type = Column(String(10), nullable=False)  # image | video
    provider = Column(String(40), nullable=False)
    model = Column(String(120), nullable=True)
    identity = Column(Text, nullable=False)  # canonical JSON of the request (no secrets)
    forced = Column(Boolean, nullable=False, default=False)  # produced by an explicit regenerate
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class AIGenerationLock(Base):
    """Held while one request generates a given identity, so identical requests wait and reuse it."""
    __tablename__ = "ai_generation_locks"

    key = Column(String(120), primary_key=True)  # "<scope_key>:<generation_hash>"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class AIGenerationRun(Base):
    """One AI generation attempt through the AI media layer (ai_media.py): which providers were tried,
    which one made the media (or why none could), when, and the asset it produced. Cache hits are not
    runs. Background generations are followed through their run; nothing here is resumed after a restart."""
    __tablename__ = "ai_generation_runs"
    __table_args__ = (Index("ix_ai_run_scope_created", "scope_key", "created_at"),)

    id = Column(String(32), primary_key=True)
    scope_key = Column(String(40), nullable=False)  # "user:<id>"
    user_id = Column(Integer, nullable=True)
    media_type = Column(String(10), nullable=False)
    status = Column(String(20), nullable=False, default="queued")  # queued | running | completed | failed | cancelled
    requested_provider = Column(String(40), nullable=True)  # an explicit choice, else the preferred provider
    provider = Column(String(40), nullable=True)  # the provider that made the media (or was tried last)
    model = Column(String(120), nullable=True)
    fallback_from = Column(String(40), nullable=True)  # the preferred provider that could not be used
    generation_hash = Column(String(64), nullable=True)  # the identity of what was made (actual provider)
    provider_job_id = Column(String(200), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    forced = Column(Boolean, nullable=False, default=False)
    cache_hit = Column(Boolean, nullable=False, default=False)  # a background request answered from the cache
    asset_id = Column(String(32), nullable=True)
    error_category = Column(String(30), nullable=True)
    error_message = Column(Text, nullable=True)  # sanitized, safe to show
    http_status = Column(Integer, nullable=True)
    detail = Column(Text, nullable=True)  # JSON: the provider choice and every attempt (no prompt, no secrets)
    instance = Column(String(16), nullable=True)  # the server process running it
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)  # refreshed while a background generation runs
    # Phase 9: recovery and resumability (added to existing databases by RUN_COLUMN_ADDITIONS)
    request = Column(Text, nullable=True)  # JSON: the provider-independent request, so the work can be resumed
    request_hash = Column(String(64), nullable=True)  # the logical request (identical requests attach to an active run)
    project_id = Column(Integer, nullable=True)
    scene_index = Column(Integer, nullable=True)
    slot = Column(String(10), nullable=True)
    batch_id = Column(String(32), nullable=True)  # a lesson batch (several scenes generated in the background)
    lease_owner = Column(String(64), nullable=True)  # the worker that owns the run now
    lease_expires_at = Column(DateTime, nullable=True)  # renewed by that worker's heartbeat; after it, recovery may take over
    recovery_count = Column(Integer, nullable=True, default=0)
    cancel_requested_at = Column(DateTime, nullable=True)
    not_before = Column(DateTime, nullable=True)  # recovery waits until then (backoff)
    last_checked_at = Column(DateTime, nullable=True)  # the provider's job was last asked about its state
    allow_duplicate = Column(Boolean, nullable=True, default=False)  # the user accepted a possible duplicate (needs attention → retry)
    kind = Column(String(20), nullable=True)  # Phase 10: the executor — NULL/"ai_media" (providers) or "manim" (sandboxed render)

# Columns Phase 9 adds to an ai_generation_runs table created by Phase 8 (create_all adds tables, not columns)
RUN_COLUMN_ADDITIONS = {
    "request": "TEXT", "request_hash": "VARCHAR(64)", "project_id": "INTEGER", "scene_index": "INTEGER",
    "slot": "VARCHAR(10)", "batch_id": "VARCHAR(32)", "lease_owner": "VARCHAR(64)", "lease_expires_at": "TIMESTAMP",
    "recovery_count": "INTEGER DEFAULT 0", "cancel_requested_at": "TIMESTAMP", "not_before": "TIMESTAMP",
    "last_checked_at": "TIMESTAMP", "allow_duplicate": "BOOLEAN DEFAULT FALSE",
    "kind": "VARCHAR(20)",  # Phase 10
}
RUN_INDEXES = {
    "ix_ai_run_status_lease": ("ai_generation_runs", ("status", "lease_expires_at")),
    "ix_ai_run_request": ("ai_generation_runs", ("scope_key", "request_hash")),
    "ix_ai_run_project": ("ai_generation_runs", ("project_id", "scene_index")),
    "ix_ai_run_batch": ("ai_generation_runs", ("batch_id",)),
}

class AIGenerationAttempt(Base):
    """One try of a generation run with one provider (Phase 9): the history a run keeps (never overwritten),
    and what recovery needs to find the work again — above all the provider's job, saved as soon as the
    provider accepted it."""
    __tablename__ = "ai_generation_attempts"
    __table_args__ = (UniqueConstraint("run_id", "number", name="uq_ai_attempt_number"),)

    id = Column(String(32), primary_key=True)
    run_id = Column(String(32), ForeignKey("ai_generation_runs.id"), nullable=False, index=True)
    number = Column(Integer, nullable=False)  # 1, 2, ... within the run
    provider = Column(String(40), nullable=False)
    model = Column(String(120), nullable=True)
    generation_hash = Column(String(64), nullable=True)  # the identity this provider makes
    state = Column(String(20), nullable=False)  # see ai_runs.AttemptState
    provider_job_id = Column(String(200), nullable=True)
    provider_job = Column(Text, nullable=True)  # JSON handle to reattach to the provider's job (no secrets)
    output_path = Column(String(512), nullable=True)  # the output on disk until it is a library asset
    asset_id = Column(String(32), nullable=True)
    error_category = Column(String(30), nullable=True)
    error_message = Column(Text, nullable=True)  # sanitized
    recovered = Column(Integer, nullable=False, default=0)  # times a recovery worker took it over
    owner = Column(String(64), nullable=True)  # the worker that made or last recovered it
    detail = Column(Text, nullable=True)  # JSON: e.g. the provider's cancel answer, retries on this provider
    started_at = Column(DateTime, default=datetime.datetime.utcnow)
    submitted_at = Column(DateTime, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

class ExportOutput(Base):
    """A file made from a finished export besides its video (see export_outputs.py): subtitles, chapters,
    an MP4 copy, the recording's timeline; and the library asset registered for each video file."""
    __tablename__ = "export_outputs"
    __table_args__ = (UniqueConstraint("export_id", "kind", name="uq_export_output_kind"),)

    id = Column(Integer, primary_key=True)
    export_id = Column(String(32), ForeignKey("video_exports.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)  # webm | mp4 | vtt | chapters | timeline
    status = Column(String(20), nullable=False, default="pending")  # pending | processing | ready | failed | unavailable
    storage_key = Column(String(512), nullable=True)  # inside EXPORTS_DIR; never sent to clients
    file_size = Column(BigInteger, nullable=True)
    mime_type = Column(String(100), nullable=True)
    asset_id = Column(String(32), nullable=True)  # the library asset of a video output
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class SourceDocument(Base):
    """An uploaded source document as extracted structure (Phase 11, source_documents.py): the page extracts
    PDF/DOCX/TXT into blocks (headings, paragraphs, lists, tables, code, captions, images) and the server keeps
    them, normalized, as the version of the document an analysis refers to. Never changed after it is stored:
    a changed file is a new version (same owner and file name, higher version)."""
    __tablename__ = "source_documents"
    __table_args__ = (Index("ix_source_doc_owner_name", "user_id", "file_name"),
                      Index("ix_source_doc_owner_hash", "user_id", "content_sha256"))

    id = Column(String(32), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    file_name = Column(String(255), nullable=False)  # display name only, never used as a path
    source_type = Column(String(10), nullable=False)  # pdf | docx | txt | text
    content_sha256 = Column(String(64), nullable=False)  # the normalized blocks: the identity analyses use
    file_sha256 = Column(String(64), nullable=True)  # the uploaded file's bytes (from the page), informative
    extractor_version = Column(Integer, nullable=False, default=1)
    version = Column(Integer, nullable=False, default=1)  # 1, 2, ... per owner and file name
    page_count = Column(Integer, nullable=True)
    language = Column(String(20), nullable=True)
    block_count = Column(Integer, nullable=False, default=0)
    char_count = Column(Integer, nullable=False, default=0)
    blocks = Column(Text, nullable=False)  # JSON list of blocks
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class DocumentAnalysis(Base):
    """The analysis of one source document version (Phase 11): structure, content, quality findings,
    recommendations and the Aadhi-ready structure, plus the user's edits (kept apart from what was found)."""
    __tablename__ = "document_analyses"

    id = Column(String(32), primary_key=True)
    document_id = Column(String(32), ForeignKey("source_documents.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    content_sha256 = Column(String(64), nullable=False)  # of the document version analysed
    analysis_version = Column(Integer, nullable=False)
    config_key = Column(String(64), nullable=False, index=True)  # analysis version + mode + provider + model
    mode = Column(String(20), nullable=False)  # structural | ai
    provider = Column(String(40), nullable=True)
    model = Column(String(120), nullable=True)
    status = Column(String(20), nullable=False, default="running")  # running | completed | failed
    run_id = Column(String(32), nullable=True)  # the Phase 9 run (kind document_analysis) of an AI analysis
    chunk_results = Column(Text, nullable=True)  # JSON {chunk id: AI findings}: checkpoints a resumed run keeps
    result = Column(Text, nullable=True)  # JSON: the analysis as produced
    edits = Column(Text, nullable=True)  # JSON: the user's edits, applied on top of the result
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

class PresenterProfile(Base):
    """A user's own presenter (Phase 12, presenters.py): a name, how they look (description, palette) and an
    optional reference picture from their asset library, for presenter providers that accept one. The built-in
    presenters (Aadhi, Aadhi Teacher, AI Teacher) are defined in code; this table only holds custom ones."""
    __tablename__ = "presenter_profiles"

    id = Column(String(32), primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    type = Column(String(20), nullable=False, default="custom")
    appearance = Column(Text, nullable=True)  # JSON: description, palette
    reference_asset_id = Column(String(32), ForeignKey("assets.id"), nullable=True)
    defaults = Column(Text, nullable=True)  # JSON: position, behaviour, expression, gesture
    version = Column(Integer, nullable=False, default=1)  # changes whenever how they look changes
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
