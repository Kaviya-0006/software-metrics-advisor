"""
Project read APIs (Phase 4) + AI Advisor endpoint (Phase 6A) + scan
history (Phase 7A) + PDF report (Phase 7B).

    GET /api/projects
    GET /api/projects/{project_id}
    GET /api/projects/{project_id}/metrics
    GET /api/projects/{project_id}/risks
    GET /api/projects/{project_id}/recommendations
    GET /api/projects/{project_id}/history
    GET /api/projects/{project_id}/architecture
    GET /api/projects/{project_id}/report

The first four are pure reads against data POST /api/projects/upload
already computed and persisted (app.analyzer.engine / scoring / risk, via
app/api/upload.py). Nothing here re-runs the scanner, the metric engine,
the normalization/health-score logic, or the risk algorithm — nothing is
recomputed, and no second risk/health implementation exists. Every value
returned is read straight from the `Project` / `Module` rows.

The recommendations endpoint also reads only persisted data — it builds a
structured context from it and hands that to app.ai.advisor, which is
solely responsible for explaining/prioritizing, never for recalculating
metrics, health score, or risk (see docs/ai-advisor.md).

The history endpoint groups existing Project rows by name (no new table —
see docs/architecture.md's "Scan history" section for why re-uploading
already preserves history, and get_project_history()'s docstring below
for the known name-collision limitation of that grouping).

The report endpoint (Phase 7B) lays out the same persisted data as a
downloadable PDF via app.reports.pdf_report — also a pure read, and it
never calls the AI provider (see get_project_report()'s docstring).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import ArchitectureScan, Module, Project, ProjectVersion
from app.database.versioning import ensure_version, iso_utc, version_row
from app.database.schemas import (
    ArchitectureResponse,
    ProjectDetailResponse,
    ProjectHistoryResponse,
    ProjectListResponse,
    ProjectMetricsResponse,
    ProjectRisksResponse,
    RecommendationsResponse,
)
from app.ai.advisor import build_ai_context, generate_recommendations
from app.reports.pdf_report import generate_project_report

router = APIRouter(prefix="/api/projects", tags=["projects"])
logger = logging.getLogger("software_metrics_advisor.reports")


def _get_project_or_404(project_id: int, db: Session) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(
            status_code=404, detail=f"Project {project_id} was not found."
        )
    return project


def _languages_list(project: Project) -> list[str]:
    if not project.languages:
        return []
    return [lang for lang in project.languages.split(",") if lang]


def _module_metrics_dict(module: Module) -> dict:
    """
    Rebuild the full per-module metrics dict from persisted columns.
    `metrics_json` already holds exactly the nested shape
    ModuleResult.to_dict() produces (see analyzer/engine.py) — we start
    from that and add the two Phase 4 spec fields Phase 2 never computed,
    both explicitly null rather than guessed:

      - test_coverage: intentionally null because uploaded source is never executed.
        The project-level Testing category is a static test-file presence score.
      - documentation_percentage: NOT fabricated — this is the same
        comment/code ratio already derived and documented as a proxy in
        docs/health-score.md §2, computed here from the same persisted
        loc_comments/loc_code columns Phase 2 stored. It is a real,
        documented derived value, not an invented one.
    """
    base = dict(module.metrics_json or {})

    base["test_coverage"] = None  # runtime coverage is not measured; source is never executed

    if module.loc_code and module.loc_code > 0:
        base["documentation_percentage"] = round(
            (module.loc_comments or 0) / module.loc_code * 100, 2
        )
    else:
        base["documentation_percentage"] = None

    return base


def _module_risk_dict(module: Module) -> dict:
    return {
        "file_name": module.file_name,
        "file_path": module.file_path,
        "risk_score": module.risk_score,
        "risk_level": module.risk_level,
        "risk_factors": module.risk_factors or [],
    }


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="List analyzed projects",
    description=(
        "Returns every uploaded scan (each is one version of a project), most "
        "recently uploaded first. Phase 9A adds `identity_id` and "
        "`version_number`; GET /api/identities lists the logical projects."
    ),
)
def list_projects(db: Session = Depends(get_db)):
    """All analyzed projects, most recently uploaded first."""
    projects = db.query(Project).order_by(Project.upload_date.desc()).all()
    return ProjectListResponse(
        projects=[
            {
                "id": p.id,
                "name": p.name,
                "upload_date": p.upload_date,
                "health_score": p.health_score,
                "health_status": p.health_status,
                "files": p.files_analyzed,
                "languages": _languages_list(p),
                # Phase 9A: stable project identity + this scan's version.
                "identity_id": p.version.identity_id if p.version else None,
                "version_number": p.version.version_number if p.version else None,
            }
            for p in projects
        ]
    )


@router.get(
    "/{project_id}",
    response_model=ProjectDetailResponse,
    summary="Get full detail for one project",
    description=(
        "Health score, health status, category scores, summary metrics, "
        "every module's raw metrics, and the risky-module ranking for one "
        "analyzed project. 404 if the project does not exist."
    ),
    responses={404: {"description": "Project not found."}},
)
def get_project_detail(project_id: int, db: Session = Depends(get_db)):
    """Full detail for one analyzed project: health, category scores,
    summary metrics, every module, and the risky-module ranking — all
    read from what POST /api/projects/upload already persisted."""
    project = _get_project_or_404(project_id, db)
    modules = (
        db.query(Module)
        .filter(Module.project_id == project.id)
        .order_by(Module.file_path)
        .all()
    )

    risky_modules = sorted(
        (
            {"file_name": m.file_name, "risk_score": m.risk_score, "risk_level": m.risk_level}
            for m in modules
            if m.risk_score is not None
        ),
        key=lambda r: r["risk_score"],
        reverse=True,
    )

    return ProjectDetailResponse(
        project={
            "id": project.id,
            "name": project.name,
            "upload_date": project.upload_date,
            "languages": _languages_list(project),
        },
        health={"score": project.health_score, "status": project.health_status},
        category_scores=project.category_scores or {},
        summary={
            "files": project.files_analyzed,
            "classes": project.total_classes,
            "functions": project.total_functions,
            "total_loc": project.total_loc,
        },
        modules=[_module_metrics_dict(m) for m in modules],
        risky_modules=risky_modules,
    )


@router.get(
    "/{project_id}/metrics",
    response_model=ProjectMetricsResponse,
    summary="Get module-level raw metrics for one project",
    description=(
        "Per-module Phase 2 metrics (LOC, complexity, maintainability, "
        "coupling, cohesion, inheritance depth, duplication) exactly as "
        "persisted, with test_coverage explicitly null (Phase 2 has no "
        "coverage data source) and a documentation_percentage proxy "
        "derived from stored comment/code line counts. 404 if the "
        "project does not exist."
    ),
    responses={404: {"description": "Project not found."}},
)
def get_project_metrics(project_id: int, db: Session = Depends(get_db)):
    """Module-level raw metrics only (no health/risk aggregation) — for a
    consumer that just wants the Phase 2 numbers back out of the database."""
    project = _get_project_or_404(project_id, db)
    modules = (
        db.query(Module)
        .filter(Module.project_id == project.id)
        .order_by(Module.file_path)
        .all()
    )
    return ProjectMetricsResponse(
        project_id=project.id,
        modules=[_module_metrics_dict(m) for m in modules],
    )


@router.get(
    "/{project_id}/risks",
    response_model=ProjectRisksResponse,
    summary="Get risk summary and ranking for one project",
    description=(
        "Counts of modules at each risk level plus the full risky-module "
        "ranking, sorted descending by risk score — all read from the "
        "Phase 3 risk values already persisted per module (no risk score "
        "is recalculated here). 404 if the project does not exist."
    ),
    responses={404: {"description": "Project not found."}},
)
def get_project_risks(project_id: int, db: Session = Depends(get_db)):
    """Risk-level counts + risky-module ranking, read from the Phase 3
    risk values already persisted per module. No risk score is
    recalculated here."""
    project = _get_project_or_404(project_id, db)
    modules = db.query(Module).filter(Module.project_id == project.id).all()

    risk_summary = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    level_key = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high", "CRITICAL": "critical"}
    for m in modules:
        if m.risk_level in level_key:
            risk_summary[level_key[m.risk_level]] += 1

    risky_modules = sorted(
        (_module_risk_dict(m) for m in modules if m.risk_score is not None),
        key=lambda r: r["risk_score"],
        reverse=True,
    )

    return ProjectRisksResponse(
        project_id=project.id,
        risk_summary=risk_summary,
        risky_modules=risky_modules,
    )


@router.get(
    "/{project_id}/recommendations",
    response_model=RecommendationsResponse,
    summary="Get AI-generated recommendations for one project",
    description=(
        "Explains this project's already-computed health score and risk "
        "results in plain language and suggests practical improvements, "
        "via an LLM. The AI Advisor never recalculates metrics, the "
        "health score, or risk scores — those persisted Phase 2/3 values "
        "are sent to it as authoritative context and are the only source "
        "of truth (see docs/ai-advisor.md). If no AI provider is "
        "configured, or the provider fails, returns a clean "
        "`available: false` response rather than fabricating advice. "
        "404 if the project does not exist."
    ),
    responses={404: {"description": "Project not found."}},
)
def get_project_recommendations(project_id: int, db: Session = Depends(get_db)):
    project = _get_project_or_404(project_id, db)
    modules = db.query(Module).filter(Module.project_id == project.id).all()

    project_info = {
        "name": project.name,
        "health_score": project.health_score,
        "health_status": project.health_status,
        "category_scores": project.category_scores or {},
        "summary": {
            "files": project.files_analyzed,
            "classes": project.total_classes,
            "functions": project.total_functions,
            "total_loc": project.total_loc,
        },
    }
    modules_info = [{**_module_metrics_dict(m), **_module_risk_dict(m)} for m in modules]

    context = build_ai_context(project_info, modules_info)
    result = generate_recommendations(context)

    return RecommendationsResponse(
        project_id=project.id,
        available=result.available,
        recommendations=[r.to_dict() for r in result.recommendations],
        message=result.message,
    )


def _get_history_rows(project: Project, db: Session) -> list[dict]:
    """
    Shared by GET /{project_id}/history and GET /{project_id}/report so there
    is exactly one implementation of the history logic.

    Phase 9A: history is the ordered list of immutable versions of the project
    *identity* this scan belongs to (see app/database/versioning.py) - not a
    grouping by file name. Each row keeps the original keys (scan_id,
    scanned_at, health_score, health_status, category_scores) and adds
    version_number, the stored headline `metrics`, the language breakdown and
    the architecture project type. `scanned_at` is the stored datetime here;
    the HTTP endpoint serializes it as UTC ISO-8601.
    """
    version = ensure_version(db, project)
    if version is None:
        return []
    versions = (
        db.query(ProjectVersion)
        .filter(ProjectVersion.identity_id == version.identity_id)
        .order_by(ProjectVersion.version_number.asc())
        .all()
    )
    return [version_row(v) for v in versions]


@router.get(
    "/{project_id}/history",
    response_model=ProjectHistoryResponse,
    summary="Get the version history of the project a scan belongs to",
    description=(
        "Phase 9A. Returns every immutable version of the project (stable "
        "identity) that `project_id` belongs to, oldest first: version number, "
        "upload time (UTC, ISO-8601 with 'Z'), health score/status, category "
        "scores, the stored headline metrics (average complexity, average "
        "maintainability, duplication %, average/max risk, high-risk module "
        "count, LOC, file/class/function counts) and the per-language file/LOC "
        "breakdown. Values are exactly what each upload stored; a metric a "
        "version never recorded is null, never 0. Any version's id returns the "
        "same project history; `current_version` says which one `project_id` "
        "is. Pure read. 404 if the given project_id does not exist."
    ),
    responses={404: {"description": "Project not found."}},
)
def get_project_history(project_id: int, db: Session = Depends(get_db)):
    project = _get_project_or_404(project_id, db)
    rows = _get_history_rows(project, db)
    for row in rows:
        row["scanned_at"] = iso_utc(row["scanned_at"])
    version = project.version
    return ProjectHistoryResponse(
        project_id=project.id,
        project_name=project.name,
        identity_id=version.identity_id if version else None,
        current_version=version.version_number if version else None,
        history=rows,
    )


@router.get(
    "/{project_id}/architecture",
    response_model=ArchitectureResponse,
    summary="Get the Architecture Intelligence result of one scan",
    description=(
        "Phase 8D. Returns the architecture result saved for exactly this "
        "scan (project_id identifies the scan: every upload is its own "
        "project row): detected layers with language/file counts, the "
        "diagram graph, matched frontend/backend API calls, potential "
        "integration issues, potentially unused endpoints and uncertain "
        "findings, each with source-file and line evidence. Pure read of "
        "what the upload stored; nothing is re-analyzed. For scans made "
        "before Phase 8D, or when the architecture step failed, the "
        "response is 200 with `available: false` and a message — a result "
        "is never invented. 404 if the project does not exist."
    ),
    responses={404: {"description": "Project not found."}},
)
def get_project_architecture(project_id: int, db: Session = Depends(get_db)):
    project = _get_project_or_404(project_id, db)
    scan = (
        db.query(ArchitectureScan)
        .filter(ArchitectureScan.project_id == project.id)
        .first()
    )

    if scan is None:
        return ArchitectureResponse(
            project_id=project.id,
            scan_id=project.id,
            project_name=project.name,
            available=False,
            message=(
                "No architecture analysis was saved for this scan (it was "
                "uploaded before Architecture Intelligence existed). "
                "Re-upload the project to analyze its architecture."
            ),
        )

    if scan.status != "analyzed" or not scan.report_json:
        return ArchitectureResponse(
            project_id=project.id,
            scan_id=project.id,
            project_name=project.name,
            available=False,
            scanned_at=scan.created_at,
            status=scan.status,
            message=scan.error_message
            or "Architecture analysis is not available for this scan.",
        )

    return ArchitectureResponse(
        project_id=project.id,
        scan_id=project.id,
        project_name=project.name,
        available=True,
        scanned_at=scan.created_at,
        status=scan.status,
        architecture=scan.report_json,
    )


@router.get(
    "/{project_id}/report",
    summary="Download a PDF Project Health Report",
    description=(
        "Generates a downloadable PDF summarizing this project's "
        "already-persisted analysis: executive summary, Health Score, "
        "category scores, risk summary and risky-module ranking, "
        "module-level details, an AI Recommendations section, and a "
        "historical trend summary when this project has more than one "
        "scan. Metrics and risk values are read from the database. The "
        "AI section reuses the same recommendation pipeline as the "
        "dashboard and includes evidence-backed AI guidance or the "
        "deterministic persisted-metric fallback when the provider is "
        "unavailable. 404 if the project does not exist."
    ),
    responses={
        200: {"content": {"application/pdf": {}}},
        404: {"description": "Project not found."},
        500: {"description": "PDF generation failed."},
    },
)
def get_project_report(project_id: int, db: Session = Depends(get_db)):
    project = _get_project_or_404(project_id, db)
    modules = (
        db.query(Module)
        .filter(Module.project_id == project.id)
        .order_by(Module.file_path)
        .all()
    )

    project_info = {
        "id": project.id,
        "name": project.name,
        "upload_date": project.upload_date,
        "health_score": project.health_score,
        "health_status": project.health_status,
        "category_scores": project.category_scores or {},
        "summary": {
            "files": project.files_analyzed,
            "classes": project.total_classes,
            "functions": project.total_functions,
            "total_loc": project.total_loc,
        },
    }
    modules_info = [{**_module_metrics_dict(m), **_module_risk_dict(m)} for m in modules]
    history_rows = _get_history_rows(project, db)

    # Keep the PDF AI section consistent with the dashboard. Recommendations
    # are generated from the same persisted project/module evidence and use
    # the same deterministic fallback when the configured provider is
    # unavailable. No source code is sent to the provider here; only the
    # structured metrics context assembled by build_ai_context().
    context = build_ai_context(project_info, modules_info)
    advisory = generate_recommendations(context)
    recommendations_info = advisory.to_dict()

    try:
        pdf_bytes = generate_project_report(
            project_info, modules_info, history_rows, recommendations_info
        )
    except Exception:
        logger.exception("PDF generation failed for project_id=%s", project_id)
        raise HTTPException(status_code=500, detail="Could not generate the PDF report.")

    filename = f"software-health-report-{project_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project_id}/quality-gate", summary="Evaluate quality gate using stored metrics")
def get_quality_gate(project_id: int, db: Session = Depends(get_db)):
    project = _get_project_or_404(project_id, db)
    modules = db.query(Module).filter(Module.project_id == project.id).all()
    health = project.health_score
    critical = sum(1 for m in modules if m.risk_level == "CRITICAL")
    duplication = [m.duplication_percentage for m in modules if m.duplication_percentage is not None] if modules else []
    # Duplication values are per-file; an unweighted mean is explicitly labelled.
    average_duplication = round(sum(duplication)/len(duplication), 2) if duplication else None
    rules = [
        {"name": "Project health >= 75", "value": health, "threshold": 75,
         "passed": health >= 75 if health is not None else None},
        {"name": "No CRITICAL-risk modules", "value": critical, "threshold": 0,
         "passed": critical == 0},
        {"name": "Mean module duplication < 15%", "value": average_duplication, "threshold": 15,
         "passed": average_duplication < 15 if average_duplication is not None else None},
    ]
    outcomes = [r["passed"] for r in rules]
    status = "FAILED" if False in outcomes else "NOT EVALUATED" if None in outcomes else "PASSED"
    return {"project_id": project_id, "status": status, "rules": rules,
            "note": "Advisory gate. Missing metrics are not treated as zero; historical regression gate not evaluated."}


@router.get("/{project_id}/language-health", summary="Stored metrics grouped by language")
def get_language_health(project_id: int, db: Session = Depends(get_db)):
    project = _get_project_or_404(project_id, db)
    modules = db.query(Module).filter(Module.project_id == project.id).all()
    groups = {}
    for m in modules:
        lang = m.language or "Unknown"
        row = groups.setdefault(lang, {"language": lang, "files": 0, "loc": 0,
            "risk_counts": {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0},
            "complexity_values": [], "maintainability_values": []})
        row["files"] += 1
        row["loc"] += m.loc_total or 0
        if m.risk_level in row["risk_counts"]:
            row["risk_counts"][m.risk_level] += 1
        if m.complexity_average is not None: row["complexity_values"].append(m.complexity_average)
        if m.maintainability_index is not None: row["maintainability_values"].append(m.maintainability_index)
    result = []
    for row in groups.values():
        for metric in ("complexity", "maintainability"):
            values = row.pop(metric + "_values")
            row["mean_" + metric] = round(sum(values)/len(values), 2) if values else None
        result.append(row)
    return {"project_id": project_id, "languages": sorted(result, key=lambda r: r["language"]),
            "note": "Means are per-file and are not cross-language calibrated health scores."}
