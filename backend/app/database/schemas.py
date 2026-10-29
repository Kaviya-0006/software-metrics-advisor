"""
Pydantic schemas for request/response validation.

Phase 1 only needs a minimal Project schema to confirm the database layer
works end to end. Upload, metrics, and recommendation schemas are added in
later phases.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectBase(BaseModel):
    name: str


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    upload_date: datetime
    health_score: float | None = None
    status: str


# --- Phase 2-3: upload / analysis response shapes ---------------------------
#
# These are intentionally loose (dict-typed nested sections) rather than a
# fully-typed nested Pydantic model tree: the module/project metric shape is
# still evolving as later phases add normalization and risk fields on top of
# it, and re-declaring the same structure analyzer/engine.py, analyzer/
# scoring.py, and analyzer/risk.py already build would just be a second
# place to keep in sync. FastAPI still validates the top-level response
# shape; the nested payloads are exactly what those modules' to_dict()
# methods produce.


class UploadResponse(BaseModel):
    success: bool
    project_id: int
    project_name: str
    analysis: dict
    health: dict  # Phase 3: {"score": float, "status": str}
    category_scores: dict  # Phase 3: per-category 0-100 scores (or null if unavailable)
    risky_modules: list[dict]  # Phase 3: modules with a risk score, sorted descending
    # Phase 8D: headline Architecture Intelligence numbers (null when the
    # architecture step failed; the full report is GET .../architecture).
    architecture: dict | None = None
    # Phase 9A: where this upload sits in project history
    # ({identity_id, version_number, is_new_project, resolution, reason,
    # warnings, suggestions}).
    version: dict | None = None


# --- Phase 4: project read-API response shapes -------------------------------
#
# Same "loose nested dict" philosophy as UploadResponse above, for the same
# reason: the module/project metric shape is defined once, in
# analyzer/engine.py / scoring.py / risk.py, and these endpoints only read
# already-persisted data back out — re-declaring the nested shape here would
# just be a second place it could drift out of sync.


class ProjectListResponse(BaseModel):
    projects: list[dict]  # each: id, name, upload_date, health_score, health_status, files, languages


class ProjectDetailResponse(BaseModel):
    project: dict
    health: dict
    category_scores: dict
    summary: dict
    modules: list[dict]
    risky_modules: list[dict]


class ProjectMetricsResponse(BaseModel):
    project_id: int
    modules: list[dict]


class ProjectRisksResponse(BaseModel):
    project_id: int
    risk_summary: dict  # {"low": int, "medium": int, "high": int, "critical": int}
    risky_modules: list[dict]


# --- Phase 6: AI Advisor response shape ---------------------------------
#
# `recommendations` is loose-typed for the same reason as everything
# above: its shape (problem/severity/explanation/recommendation/priority)
# is defined once, in app/ai/advisor.py's Recommendation dataclass, and
# validated there — not re-declared here.


class RecommendationsResponse(BaseModel):
    project_id: int
    available: bool
    recommendations: list[dict]
    message: str | None = None


# --- Phase 7A: scan history response shape -----------------------------
#
# Same loose-dict philosophy as above. `history` entries are built
# directly from persisted Project rows — see app/api/projects.py's
# get_project_history() for exactly which fields are included and how
# scans are grouped.


class ProjectHistoryResponse(BaseModel):
    project_id: int
    project_name: str
    # Phase 9A: stable project identity and which version `project_id` is.
    identity_id: int | None = None
    current_version: int | None = None
    history: list[dict]


# --- Phase 8D: Architecture Intelligence response shape -----------------
#
# `architecture` is the report built by app/analyzer/architecture_report.py,
# returned exactly as persisted. `available: false` is a normal 200 response
# (older scans made before Phase 8D, or an architecture step that failed) —
# the result is never recomputed or invented on read, because the uploaded
# source is deleted after the scan.


class ArchitectureResponse(BaseModel):
    project_id: int
    scan_id: int
    project_name: str
    available: bool
    scanned_at: datetime | None = None
    status: str | None = None
    message: str | None = None
    architecture: dict | None = None


# --- Phase 9A: project identities / versions -----------------------------


class IdentityListResponse(BaseModel):
    identities: list[dict]


class IdentityDetailResponse(BaseModel):
    identity: dict
    versions: list[dict]


# --- Phase 9B: version comparison -----------------------------------------


class VersionComparisonResponse(BaseModel):
    identity_id: int
    project_name: str
    # False for a project that has only one version (nothing to compare yet);
    # `reason` then explains it and `comparison` is null.
    available: bool
    reason: str | None = None
    swapped: bool = False  # true when the two requested versions were reordered oldest -> newest
    versions: list[dict]  # every version of the project (for the selectors)
    comparison: dict | None = None
