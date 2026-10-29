"""
SQLAlchemy ORM models.

Phase 1 defined the minimal Project model. Phase 2 added the Module table
for per-file raw analysis results plus project-level scan info. Phase 3
adds Health Score / category-score fields on Project and risk fields on
Module. metrics_history and AI-recommendation tables are added in Phase 4+
(see docs/architecture.md and the project master specification).
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Boolean,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import relationship

from app.database.database import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    upload_date = Column(DateTime, default=datetime.utcnow)
    health_score = Column(Float, nullable=True)  # Phase 3
    status = Column(String, default="pending")  # workflow state: pending/analyzed/failed

    # Phase 2: project-level scan info, filled in right after analysis.
    languages = Column(String, nullable=True)  # comma-separated, e.g. "python,java"
    files_analyzed = Column(Integer, nullable=True)
    total_classes = Column(Integer, nullable=True)
    total_functions = Column(Integer, nullable=True)
    total_loc = Column(Integer, nullable=True)

    # Phase 3: Project Health Score outputs. `health_status` is the
    # CRITICAL/POOR/NEEDS_ATTENTION/HEALTHY/EXCELLENT band — kept separate
    # from `status` above, which tracks upload/analysis workflow state, not
    # code health.
    health_status = Column(String, nullable=True)
    category_scores = Column(JSON, nullable=True)

    modules = relationship("Module", back_populates="project", cascade="all, delete-orphan")

    # Phase 8D: at most one Architecture Intelligence result per scan (each
    # upload is its own Project row, so this *is* the per-scan result).
    architecture_scan = relationship(
        "ArchitectureScan",
        back_populates="project",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Phase 9A: the immutable version record that places this scan in a
    # project's history (None only for a scan that predates Phase 9A and has not
    # been backfilled yet - init_db() backfills on startup).
    version = relationship(
        "ProjectVersion",
        back_populates="scan",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Phase 9B: per-file content hashes captured at upload time.
    file_fingerprints = relationship(
        "FileFingerprint",
        back_populates="scan",
        cascade="all, delete-orphan",
    )


class Module(Base):
    """One row per analyzed source file. Flattened scalar columns cover
    the metrics used for querying/sorting later phases will want (e.g.
    Phase 3 risk ranking); `metrics_json` also keeps the complete nested
    result (matching the module schema returned by the API) so nothing
    computed in Phase 2 is lost even if a later phase needs a field that
    doesn't have its own column."""

    __tablename__ = "modules"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)

    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    language = Column(String, nullable=False)

    loc_total = Column(Integer, nullable=True)
    loc_code = Column(Integer, nullable=True)
    loc_comments = Column(Integer, nullable=True)
    loc_blank = Column(Integer, nullable=True)

    classes = Column(Integer, nullable=True)
    functions = Column(Integer, nullable=True)

    complexity_total = Column(Integer, nullable=True)
    complexity_average = Column(Float, nullable=True)
    complexity_maximum = Column(Integer, nullable=True)

    maintainability_index = Column(Float, nullable=True)
    coupling = Column(Integer, nullable=True)
    cohesion = Column(Float, nullable=True)
    depth_of_inheritance = Column(Float, nullable=True)
    duplication_percentage = Column(Float, nullable=True)
    duplicated_lines = Column(Integer, nullable=True)

    parse_error = Column(Text, nullable=True)

    # Phase 3: rule-based risk detection output for this module.
    risk_score = Column(Float, nullable=True)
    risk_level = Column(String, nullable=True)
    risk_factors = Column(JSON, nullable=True)  # list[str]

    # Full nested metric result (see analyzer/engine.py ModuleResult.to_dict),
    # kept as JSON for flexibility in later phases.
    metrics_json = Column(JSON, nullable=True)

    project = relationship("Project", back_populates="modules")


class ArchitectureScan(Base):
    """
    Phase 8D: the Architecture Intelligence result of one scan.

    Every upload creates its own `Project` row, so a unique `project_id` here
    ties the architecture result to exactly the scan that produced it —
    re-uploading the same project name never overwrites an older scan's result.

    `report_json` holds the complete report built by
    `analyzer/architecture_report.build_architecture_report`; the scalar
    columns duplicate its headline numbers for querying. `status` is
    "analyzed", or "failed" when the architecture step raised (the metric scan
    itself still succeeds; `error_message` is a generic note, the real
    exception goes to the server log). Tables are created with `create_all`,
    so existing databases gain this table on the next start without touching
    any existing table.
    """

    __tablename__ = "architecture_scans"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id"), nullable=False, unique=True, index=True
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    schema_version = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False, default="analyzed")
    error_message = Column(Text, nullable=True)

    project_type = Column(String, nullable=True)
    confidence = Column(String, nullable=True)
    total_files = Column(Integer, nullable=True)
    total_loc = Column(Integer, nullable=True)
    endpoints_found = Column(Integer, nullable=True)
    calls_found = Column(Integer, nullable=True)
    matched_count = Column(Integer, nullable=True)
    unmatched_calls_count = Column(Integer, nullable=True)
    unused_endpoints_count = Column(Integer, nullable=True)
    uncertain_count = Column(Integer, nullable=True)

    report_json = Column(JSON, nullable=True)

    project = relationship("Project", back_populates="architecture_scan")


class ProjectIdentity(Base):
    """
    Phase 9A: the stable identity of a logical project across uploads.

    `projects` rows remain one-per-upload "scan snapshots" (every earlier API is
    keyed by that scan id); an identity groups the scans that are versions of
    the same project, whatever the ZIP file or its root folder was called.
    `name` is the display name (first upload, or an explicit override);
    `name_keys` are the normalized, non-generic names seen so far (ZIP file name
    and root folder) used as one - never the only - matching signal.
    """

    __tablename__ = "project_identities"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    normalized_name = Column(String, nullable=True, index=True)
    name_keys = Column(JSON, nullable=True)  # list[str]
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    versions = relationship(
        "ProjectVersion",
        back_populates="identity",
        order_by="ProjectVersion.version_number",
        cascade="all, delete-orphan",
    )


class ProjectVersion(Base):
    """
    Phase 9A: one immutable version of a project = one successful upload.

    Written once, in the same transaction as the scan it describes (so a failed
    upload leaves no version), and never updated afterwards (enforced by an ORM
    `before_update` guard below). Holds the headline numbers needed for trends,
    copied from what that scan's analysis produced - nothing here is derived
    later from newer data. Detailed per-module metrics stay in `modules` and the
    architecture result in `architecture_scans`, both keyed by `scan_id`.

    A metric that a scan genuinely did not record is NULL, never 0.
    `is_backfilled` marks versions created by the Phase 9A migration from
    scans stored earlier (some project-level numbers, e.g. duplication
    percentage, were not stored per scan back then and stay NULL).
    """

    __tablename__ = "project_versions"
    __table_args__ = (
        UniqueConstraint("identity_id", "version_number", name="uq_project_version_number"),
    )

    id = Column(Integer, primary_key=True, index=True)
    identity_id = Column(Integer, ForeignKey("project_identities.id"), nullable=False, index=True)
    scan_id = Column(Integer, ForeignKey("projects.id"), nullable=False, unique=True, index=True)
    version_number = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    health_score = Column(Float, nullable=True)
    health_status = Column(String, nullable=True)
    category_scores = Column(JSON, nullable=True)

    average_complexity = Column(Float, nullable=True)
    total_complexity = Column(Integer, nullable=True)
    average_maintainability = Column(Float, nullable=True)
    duplication_percentage = Column(Float, nullable=True)
    duplicated_lines = Column(Integer, nullable=True)

    average_risk_score = Column(Float, nullable=True)
    max_risk_score = Column(Float, nullable=True)
    risk_summary = Column(JSON, nullable=True)  # {"low","medium","high","critical"} module counts
    high_risk_modules = Column(Integer, nullable=True)  # high + critical

    total_loc = Column(Integer, nullable=True)
    files_analyzed = Column(Integer, nullable=True)
    total_classes = Column(Integer, nullable=True)
    total_functions = Column(Integer, nullable=True)
    language_breakdown = Column(JSON, nullable=True)  # {language: {"files": n, "loc": n}}
    architecture_project_type = Column(String, nullable=True)

    fingerprint = Column(JSON, nullable=True)  # structure used for identity matching
    resolution = Column(String, nullable=True)  # explicit | auto-matched | new | backfilled
    resolution_reason = Column(Text, nullable=True)
    is_backfilled = Column(Boolean, nullable=False, default=False)

    identity = relationship("ProjectIdentity", back_populates="versions")
    scan = relationship("Project", back_populates="version")


@event.listens_for(ProjectVersion, "before_update")
def _versions_are_immutable(mapper, connection, target):  # noqa: ARG001
    raise ValueError("Project versions are immutable and cannot be updated.")


class FileFingerprint(Base):
    """
    Phase 9B: SHA-256 and size of one analyzed source file, captured while the
    uploaded ZIP is extracted (the source itself is deleted afterwards).

    Comparing two versions' hashes is the only way to *prove* a file is
    unchanged or merely moved: metrics can coincide for different content.
    `file_path` is exactly the path stored on the matching `modules` row.
    Scans stored before Phase 9B have no rows here; comparisons against them
    degrade to metric-only evidence and say so. Written once with the scan,
    never updated.
    """

    __tablename__ = "file_fingerprints"
    __table_args__ = (
        UniqueConstraint("scan_id", "file_path", name="uq_fingerprint_scan_path"),
    )

    id = Column(Integer, primary_key=True, index=True)
    scan_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    file_path = Column(String, nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=True)

    scan = relationship("Project", back_populates="file_fingerprints")
