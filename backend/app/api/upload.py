"""
Project upload + analysis API.

    POST /api/projects/upload

Pipeline: validate upload -> save to a temp file -> safe ZIP extraction
(app.analyzer.scanner) -> full metric analysis (app.analyzer.engine) ->
Phase 8D Architecture Intelligence over the same parse
(app.analyzer.architecture_report) ->
Phase 3 scoring: per-module normalization + Project Health Score
(app.analyzer.scoring) and rule-based module risk detection
(app.analyzer.risk) -> persist Project + Module rows -> return structured
JSON.

The uploaded source code is never executed — only scanned and parsed.
"""

from __future__ import annotations

import hashlib
from collections import Counter
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analyzer.comparison import calculate_code_churn
from app.analyzer.architecture_report import (
    SCHEMA_VERSION as ARCHITECTURE_SCHEMA_VERSION,
    build_architecture_report,
    summarize_report,
)
from app.analyzer.identity import (
    Candidate,
    auto_resolve,
    build_fingerprint,
    normalize_name,
    rank_candidates,
    structure_similarity,
)
from app.analyzer.engine import ModuleResult, ProjectAnalysis, analyze_project
from app.analyzer.risk import calculate_module_risk, rank_risky_modules
from app.analyzer.scanner import UnsafeZipError, discover_source_files, safe_extract_zip
from app.analyzer.scoring import calculate_project_health, score_module
from app.config import settings
from app.database.database import get_db
from app.database.models import (
    ArchitectureScan,
    FileFingerprint,
    Module,
    Project,
    ProjectIdentity,
    ProjectVersion,
)
from app.database.versioning import (
    identity_summaries,
    merge_name_keys,
    next_version_number,
    snapshot_from_analysis,
)
from app.database.schemas import UploadResponse

router = APIRouter(prefix="/api/projects", tags=["projects"])
logger = logging.getLogger("software_metrics_advisor.architecture")

ARCHITECTURE_FAILED_MESSAGE = (
    "Architecture analysis could not be completed for this scan. "
    "The metric analysis is unaffected."
)


def _project_name_from_filename(filename: str) -> str:
    return Path(filename).stem or "uploaded_project"


def _save_upload_to_temp(upload: UploadFile, destination: Path) -> int:
    """Stream the upload to disk, enforcing MAX_UPLOAD_SIZE_MB as we go
    (rather than trusting a Content-Length header, which can be absent or
    wrong). Returns the number of bytes written."""
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    written = 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as out_file:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                out_file.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"Upload exceeds the {settings.MAX_UPLOAD_SIZE_MB} MB "
                        "limit."
                    ),
                )
            out_file.write(chunk)

    return written


_MODULE_FIELDS_FOR_CHURN = (
    "file_path", "language", "loc_total", "loc_code", "loc_comments", "loc_blank",
    "classes", "functions", "complexity_total", "complexity_average",
    "complexity_maximum", "maintainability_index", "coupling", "cohesion",
    "depth_of_inheritance", "duplication_percentage", "duplicated_lines",
    "risk_score", "risk_level", "parse_error",
)

def _flat_comparison_modules(analysis: ProjectAnalysis, module_risks: dict) -> list[dict]:
    """Flatten the current in-memory analysis into the fields used by the
    version-comparison engine. No metric is recomputed here."""
    rows = []
    for module in analysis.modules:
        risk = module_risks[module.file_path]
        rows.append(
            {
                "file_path": module.file_path,
                "language": module.language,
                "loc_total": module.loc.total,
                "loc_code": module.loc.code,
                "loc_comments": module.loc.comments,
                "loc_blank": module.loc.blank,
                "classes": module.classes,
                "functions": module.functions,
                "complexity_total": module.complexity_total,
                "complexity_average": module.complexity_average,
                "complexity_maximum": module.complexity_maximum,
                "maintainability_index": module.maintainability_index,
                "coupling": module.coupling,
                "cohesion": module.cohesion,
                "depth_of_inheritance": module.depth_of_inheritance,
                "duplication_percentage": module.duplication_percentage,
                "duplicated_lines": module.duplicated_lines,
                "risk_score": risk.risk_score,
                "risk_level": risk.risk_level,
                "parse_error": module.parse_error,
            }
        )
    return rows


def _persist_analysis(
    db: Session,
    project_name: str,
    analysis: ProjectAnalysis,
    health,  # ProjectHealth
    module_risks: dict,  # file_path -> ModuleRisk
    architecture_report: dict | None = None,  # Phase 8D (None => step failed)
    *,
    placement: dict | None = None,  # Phase 9A: where this scan sits in project history
    fingerprints: dict | None = None,  # Phase 9B: {file_path: (sha256, size_bytes)}
) -> tuple[Project, ProjectVersion | None]:
    project = Project(
        name=project_name,
        status="analyzed",
        languages=",".join(analysis.languages),
        files_analyzed=analysis.files_analyzed,
        total_classes=analysis.total_classes,
        total_functions=analysis.total_functions,
        total_loc=analysis.total_loc,
        health_score=health.health_score,
        health_status=health.health_status,
        category_scores=health.category_scores.to_dict(),
    )
    db.add(project)
    db.flush()  # assigns project.id without committing yet

    for module_result in analysis.modules:  # type: ModuleResult
        risk = module_risks[module_result.file_path]
        db.add(
            Module(
                project_id=project.id,
                file_name=module_result.file_name,
                file_path=module_result.file_path,
                language=module_result.language,
                loc_total=module_result.loc.total,
                loc_code=module_result.loc.code,
                loc_comments=module_result.loc.comments,
                loc_blank=module_result.loc.blank,
                classes=module_result.classes,
                functions=module_result.functions,
                complexity_total=module_result.complexity_total,
                complexity_average=module_result.complexity_average,
                complexity_maximum=module_result.complexity_maximum,
                maintainability_index=module_result.maintainability_index,
                coupling=module_result.coupling,
                cohesion=module_result.cohesion,
                depth_of_inheritance=module_result.depth_of_inheritance,
                duplication_percentage=module_result.duplication_percentage,
                duplicated_lines=module_result.duplicated_lines,
                parse_error=module_result.parse_error,
                risk_score=risk.risk_score,
                risk_level=risk.risk_level,
                risk_factors=risk.risk_factors,
                metrics_json=module_result.to_dict(),
            )
        )

    # Phase 8D: stored in the same transaction as the project, keyed to this
    # exact scan. A failed architecture step is recorded as such (never as an
    # empty "successful" result).
    if architecture_report is not None:
        headline = summarize_report(architecture_report)
        db.add(
            ArchitectureScan(
                project_id=project.id,
                schema_version=architecture_report["schema_version"],
                status="analyzed",
                project_type=headline["project_type"],
                confidence=headline["confidence"],
                total_files=architecture_report["summary"]["total_files"],
                total_loc=architecture_report["summary"]["total_loc"],
                endpoints_found=headline["total_endpoints"],
                calls_found=headline["total_calls"],
                matched_count=headline["matched"],
                unmatched_calls_count=headline["unmatched_frontend_calls"],
                unused_endpoints_count=headline["unused_backend_endpoints"],
                uncertain_count=headline["uncertain"],
                report_json=architecture_report,
            )
        )
    else:
        db.add(
            ArchitectureScan(
                project_id=project.id,
                schema_version=ARCHITECTURE_SCHEMA_VERSION,
                status="failed",
                error_message=ARCHITECTURE_FAILED_MESSAGE,
            )
        )

    # Phase 9B: content hashes of the analyzed files (same transaction).
    for file_path, (digest, size) in (fingerprints or {}).items():
        db.add(FileFingerprint(scan_id=project.id, file_path=file_path, sha256=digest, size_bytes=size))

    # Phase 9A: place the scan in its project's history - a new identity (V1)
    # or the next version of an existing one - in this same transaction, so a
    # failure anywhere above or here leaves no scan, no module rows and no
    # half-written version.
    version = None
    if placement is not None:
        if placement.get("identity_id") is None:
            identity = ProjectIdentity(
                name=placement["display_name"],
                normalized_name=normalize_name(placement["display_name"]),
                name_keys=[],
            )
            db.add(identity)
            db.flush()
        else:
            identity = db.get(ProjectIdentity, placement["identity_id"])
            if identity is None:  # deleted between the check and now
                raise HTTPException(status_code=404, detail="The selected project no longer exists.")
        merge_name_keys(identity, placement["zip_name"], placement["snapshot"]["fingerprint"].get("root_folder"))
        version = ProjectVersion(
            identity_id=identity.id,
            scan_id=project.id,
            version_number=next_version_number(db, identity.id),
            resolution=placement["resolution"],
            resolution_reason=placement["reason"],
            is_backfilled=False,
            **placement["snapshot"],
        )
        db.add(version)
        db.flush()

    db.commit()
    db.refresh(project)
    return project, version


def _fingerprint_files(extracted_path: Path, modules) -> dict[str, tuple[str, int]]:
    """Phase 9B: SHA-256 + size of every analyzed file, keyed by the module's
    stored path. A file that cannot be read is simply left out (the comparison
    then reports it as unverifiable); it never fails the upload."""
    root = extracted_path.resolve()
    fingerprints: dict[str, tuple[str, int]] = {}
    for module in modules:
        try:
            target = (extracted_path / module.file_path).resolve()
            target.relative_to(root)  # never read outside the extraction directory
            digest = hashlib.sha256()
            size = 0
            with open(target, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            fingerprints[module.file_path] = (digest.hexdigest(), size)
        except (OSError, ValueError):
            logger.warning("Could not fingerprint %s", module.file_path)
    return fingerprints


def _persist_with_retry(db: Session, attempts: int = 3, **kwargs):
    """Persist scan + version atomically. A concurrent upload to the same
    project can claim the same version number first; the unique constraint then
    rejects the loser, which is rolled back completely and retried with the next
    number. Any other failure rolls back and propagates - nothing partial stays."""
    for attempt in range(attempts):
        try:
            return _persist_analysis(db, **kwargs)
        except IntegrityError:
            db.rollback()
            if attempt == attempts - 1:
                raise
        except Exception:
            db.rollback()
            raise


def _analyze_architecture(analysis: ProjectAnalysis) -> dict | None:
    """Phase 8D: run the Architecture Intelligence analyzers over the single
    parse the metric pipeline already produced. Any failure is logged and
    reported as None so it can never break the metric scan."""
    try:
        return build_architecture_report(analysis.modules, analysis.parsed_modules)
    except Exception:  # noqa: BLE001 - heuristic analyzers must not sink the upload
        logger.exception("Architecture analysis failed")
        return None
    finally:
        # The parse trees are only needed for this step; release them.
        analysis.parsed_modules = []


def _validate_zip_name(file: UploadFile) -> None:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=400, detail="Only .zip project archives are accepted."
        )


def _extract_upload(file: UploadFile) -> tuple[Path, Path]:
    """Save + safely extract the upload. Returns (upload_root, extracted_path);
    the caller removes upload_root."""
    upload_root = settings.UPLOAD_DIR / uuid.uuid4().hex
    zip_path = upload_root / "upload.zip"
    extracted_path = upload_root / "extracted"
    try:
        _save_upload_to_temp(file, zip_path)
        try:
            safe_extract_zip(zip_path, extracted_path)
        except UnsafeZipError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BaseException:
        shutil.rmtree(upload_root, ignore_errors=True)
        raise
    return upload_root, extracted_path


NO_SOURCE_MESSAGE = (
    "No supported source files (.py, .java, .c, .cpp, .cc, .cxx, .h, .hpp, "
    ".js, .jsx, .ts, .tsx) were found in the upload."
)


def _candidates_for(db: Session, zip_name: str, fingerprint: dict) -> list[Candidate]:
    return rank_candidates(zip_name, fingerprint, identity_summaries(db))


@router.post(
    "/suggest-match",
    summary="Suggest existing projects that an upload may be a new version of",
    description=(
        "Phase 9A. Reads only the ZIP's file names and structure (no metric "
        "analysis, nothing is saved) and lists existing projects that look like "
        "the same project, with the reasons. A matching file name alone never "
        "qualifies a project for automatic attachment: the source-file structure "
        "must also be similar, and generic names ('download', 'project', ...) "
        "never match by name. The response says which project, if any, a plain "
        "upload would be attached to automatically."
    ),
    responses={400: {"description": "Not a .zip, unsafe archive, or no supported source files."}},
)
def suggest_match(file: UploadFile = File(...), db: Session = Depends(get_db)):
    _validate_zip_name(file)
    upload_root, extracted_path = _extract_upload(file)
    try:
        source_files = discover_source_files(extracted_path)
        if not source_files:
            raise HTTPException(status_code=400, detail=NO_SOURCE_MESSAGE)
        languages: dict[str, int] = {}
        for sf in source_files:
            languages[sf.language] = languages.get(sf.language, 0) + 1
        fingerprint = build_fingerprint((sf.relative_path for sf in source_files), languages)
        candidates = _candidates_for(db, file.filename, fingerprint)
        resolution = auto_resolve(candidates)
        return {
            "suggested_name": _project_name_from_filename(file.filename),
            "source_files": len(source_files),
            "languages": languages,
            "root_folder": fingerprint["root_folder"],
            "suggestions": [c.to_dict() for c in candidates],
            "auto_match_identity_id": resolution.identity_id,
            "auto_match_reason": resolution.reason,
        }
    finally:
        shutil.rmtree(upload_root, ignore_errors=True)


@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="Upload and analyze a project ZIP (new project or new version)",
    description=(
        "Accepts a .zip archive of a Python, Java, C, C++, JavaScript and/or TypeScript project, safely "
        "extracts it, scans and parses supported source files, computes "
        "every Phase 2 metric, normalizes them into a Project Health "
        "Score and category scores (Phase 3), runs rule-based module "
        "risk detection, persists everything, and returns the full "
        "result. The uploaded source is never executed. "
        "Phase 9A project identity: pass `identity_id` to add a new version to "
        "that existing project, `create_new=true` to force a new project (optionally "
        "named with `project_name`), or neither to let the server attach the upload to "
        "an existing project only when exactly one matches by name AND structure "
        "(otherwise a new project is created and the response lists suggestions). "
        "Every successful upload saves one immutable version; a failed upload saves nothing."
    ),
    responses={
        400: {"description": "Invalid upload: not a .zip, unsafe archive contents, no supported source files, or conflicting project options."},
        404: {"description": "The selected existing project does not exist."},
        413: {"description": "Upload exceeds the configured size limit."},
    },
)
def upload_project(
    file: UploadFile = File(...),
    identity_id: int | None = Form(None),
    create_new: bool = Form(False),
    project_name: str | None = Form(None),
    db: Session = Depends(get_db),
):
    _validate_zip_name(file)

    if identity_id is not None and create_new:
        raise HTTPException(
            status_code=400,
            detail="Choose either an existing project (identity_id) or create_new, not both.",
        )
    explicit_identity = None
    if identity_id is not None:
        explicit_identity = db.get(ProjectIdentity, identity_id)
        if explicit_identity is None:
            raise HTTPException(status_code=404, detail=f"Project {identity_id} was not found.")
    custom_name = (project_name or "").strip() or None
    if custom_name and len(custom_name) > 120:
        raise HTTPException(status_code=400, detail="project_name is too long (max 120 characters).")

    upload_root, extracted_path = _extract_upload(file)
    try:
        analysis = analyze_project(extracted_path, retain_parsed=True)

        if analysis.files_analyzed == 0:
            raise HTTPException(status_code=400, detail=NO_SOURCE_MESSAGE)

        # --- Phase 3: normalize metrics + compute deterministic module risk ---
        module_scores = [score_module(m) for m in analysis.modules]
        module_risks = {
            m.file_path: calculate_module_risk(m, scores)
            for m, scores in zip(analysis.modules, module_scores)
        }
        risky_modules = rank_risky_modules(list(module_risks.values()))

        architecture_report = _analyze_architecture(analysis)
        fingerprints = _fingerprint_files(extracted_path, analysis.modules)  # Phase 9B

        # --- Phase 9A: which project is this a version of? ----------------
        # Identity resolution must happen before Code Churn can be calculated,
        # because churn needs the immediately previous version of this project.
        current_modules = _flat_comparison_modules(analysis, module_risks)
        fingerprint = build_fingerprint(
            (m.file_path for m in analysis.modules),
            dict(Counter(m.language for m in analysis.modules)),
        )
        candidates = _candidates_for(db, file.filename, fingerprint)
        warnings: list[str] = []

        if explicit_identity is not None:
            target_id = explicit_identity.id
            display_name = explicit_identity.name
            resolution = "explicit"
            latest = explicit_identity.versions[-1].fingerprint if explicit_identity.versions else None
            reason = f"Added as a new version of '{display_name}' at your request."
            if latest and structure_similarity(fingerprint, latest) < 0.2:
                warnings.append(
                    "This upload shares very little source-file structure with the selected "
                    "project's latest version. It was saved as a new version because you chose "
                    "that project; if that was a mistake, upload it again as a new project."
                )
        elif create_new:
            target_id = None
            display_name = custom_name or _project_name_from_filename(file.filename)
            resolution = "new"
            reason = "Created as a new project at your request."
        else:
            decision = auto_resolve(candidates)
            target_id = decision.identity_id
            resolution = "auto-matched" if target_id is not None else "new"
            reason = decision.reason
            if target_id is not None:
                display_name = next(c.name for c in candidates if c.identity_id == target_id)
            else:
                display_name = custom_name or _project_name_from_filename(file.filename)

        previous_modules = None
        previous_hashes: dict[str, str] = {}
        if target_id is not None:
            target_identity = db.get(ProjectIdentity, target_id)
            previous_version = max(target_identity.versions, key=lambda v: v.version_number) if target_identity and target_identity.versions else None
            if previous_version is not None:
                previous_modules = [
                    {field: getattr(m, field) for field in _MODULE_FIELDS_FOR_CHURN}
                    for m in db.query(Module).filter(Module.project_id == previous_version.scan_id).all()
                ]
                previous_hashes = {
                    f.file_path: f.sha256
                    for f in db.query(FileFingerprint).filter(FileFingerprint.scan_id == previous_version.scan_id).all()
                }

        churn = calculate_code_churn(
            previous_modules,
            current_modules,
            previous_hashes,
            {path: digest for path, (digest, _size) in fingerprints.items()},
        )
        health = calculate_project_health(
            analysis,
            module_scores,
            code_churn_score=churn["score"],
        )
        snapshot = snapshot_from_analysis(analysis, health, module_risks, architecture_report)

        project, version = _persist_with_retry(
            db,
            project_name=display_name,
            analysis=analysis,
            health=health,
            module_risks=module_risks,
            architecture_report=architecture_report,
            fingerprints=fingerprints,
            placement={
                "identity_id": target_id,
                "display_name": display_name,
                "zip_name": file.filename,
                "resolution": resolution,
                "reason": reason,
                "snapshot": snapshot,
            },
        )

        # Embed each module's risk info into its analysis dict so a single
        # `analysis.modules[i]` entry carries both raw metrics and risk —
        # consumers don't have to cross-reference two lists.
        analysis_dict = analysis.to_dict()
        for module_dict in analysis_dict["modules"]:
            risk = module_risks[module_dict["file_path"]]
            module_dict["risk_score"] = risk.risk_score
            module_dict["risk_level"] = risk.risk_level
            module_dict["risk_factors"] = risk.risk_factors

        return UploadResponse(
            success=True,
            project_id=project.id,
            project_name=project.name,
            analysis=analysis_dict,
            health={"score": health.health_score, "status": health.health_status},
            category_scores=health.category_scores.to_dict(),
            risky_modules=risky_modules,
            architecture=(
                summarize_report(architecture_report)
                if architecture_report is not None
                else None
            ),
            version={
                "identity_id": version.identity_id,
                "version_number": version.version_number,
                "is_new_project": version.version_number == 1,
                "resolution": resolution,
                "reason": reason,
                "warnings": warnings,
                "suggestions": [
                    c.to_dict() for c in candidates if c.identity_id != version.identity_id
                ],
            },
        )
    finally:
        # We keep only the metric results (now in the database); the
        # extracted source itself does not need to remain on disk.
        shutil.rmtree(upload_root, ignore_errors=True)
