"""
Phase 9A: version-snapshot construction, identity lookup and the startup
backfill. Everything here reads values that already exist (the analysis that
just ran, or rows stored by earlier phases); it never recomputes metrics with a
second implementation of any formula.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analyzer.identity import build_fingerprint, name_keys_for, normalize_name
from app.database.models import ArchitectureScan, Module, Project, ProjectIdentity, ProjectVersion

logger = logging.getLogger("software_metrics_advisor.versioning")

_LEVEL_KEY = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high", "CRITICAL": "critical"}


def iso_utc(value: datetime | None) -> str | None:
    """Naive UTC datetimes (how this app stores them) -> ISO-8601 with a 'Z'
    so browsers do not misread them as local time."""
    if value is None:
        return None
    return value.isoformat() + ("Z" if value.tzinfo is None else "")


def language_breakdown(modules: Iterable[Any]) -> dict[str, dict[str, int]]:
    """{language: {"files": n, "loc": n}} from analyzed modules (ModuleResult or
    Module rows - both expose `language` and a LOC total)."""
    files: Counter = Counter()
    loc: Counter = Counter()
    for m in modules:
        language = m.language
        files[language] += 1
        total = getattr(m, "loc_total", None)
        if total is None:
            total = getattr(getattr(m, "loc", None), "total", 0)
        loc[language] += int(total or 0)
    return {
        lang: {"files": files[lang], "loc": loc[lang]}
        for lang in sorted(files, key=lambda k: (-files[k], k))
    }


def risk_snapshot(risk_rows: Iterable[tuple[float | None, str | None]]) -> dict[str, Any]:
    """Headline risk numbers from (risk_score, risk_level) pairs."""
    summary = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    scores: list[float] = []
    for score, level in risk_rows:
        if level in _LEVEL_KEY:
            summary[_LEVEL_KEY[level]] += 1
        if score is not None:
            scores.append(float(score))
    return {
        "average_risk_score": round(sum(scores) / len(scores), 2) if scores else None,
        "max_risk_score": round(max(scores), 2) if scores else None,
        "risk_summary": summary,
        "high_risk_modules": summary["high"] + summary["critical"],
    }


def snapshot_from_analysis(analysis, health, module_risks: dict, architecture_report: dict | None) -> dict[str, Any]:
    """Version columns for a scan that has just been analyzed (values taken
    straight from the ProjectAnalysis / ProjectHealth / ModuleRisk objects)."""
    risks = risk_snapshot((r.risk_score, r.risk_level) for r in module_risks.values())
    fingerprint = build_fingerprint(
        (m.file_path for m in analysis.modules),
        dict(Counter(m.language for m in analysis.modules)),
    )
    return {
        "health_score": health.health_score,
        "health_status": health.health_status,
        "category_scores": health.category_scores.to_dict(),
        "average_complexity": analysis.average_complexity,
        "total_complexity": analysis.total_complexity,
        "average_maintainability": analysis.average_maintainability,
        "duplication_percentage": analysis.duplication_percentage,
        "duplicated_lines": analysis.duplicated_lines,
        **risks,
        "total_loc": analysis.total_loc,
        "files_analyzed": analysis.files_analyzed,
        "total_classes": analysis.total_classes,
        "total_functions": analysis.total_functions,
        "language_breakdown": language_breakdown(analysis.modules),
        "architecture_project_type": (architecture_report or {}).get("project_type"),
        "fingerprint": fingerprint,
    }


def snapshot_from_stored_scan(project: Project, modules: list[Module], architecture_type: str | None) -> dict[str, Any]:
    """
    Version columns for a scan stored before Phase 9A, rebuilt from its stored
    module rows. Averages use the same definitions as analyzer/engine.py (over
    modules that parsed successfully). The project-level duplication percentage
    is not derivable from per-module rows, so it stays NULL rather than being
    approximated.
    """
    ok = [m for m in modules if m.parse_error is None]
    complexities = [m.complexity_average for m in ok if (m.complexity_total or 0) > 0 and m.complexity_average is not None]
    mi = [m.maintainability_index for m in ok if m.maintainability_index is not None]
    risks = risk_snapshot((m.risk_score, m.risk_level) for m in modules)
    return {
        "health_score": project.health_score,
        "health_status": project.health_status,
        "category_scores": project.category_scores or {},
        "average_complexity": round(sum(complexities) / len(complexities), 2) if complexities else (0.0 if ok else None),
        "total_complexity": sum(m.complexity_total or 0 for m in ok) if ok else None,
        "average_maintainability": round(sum(mi) / len(mi), 2) if mi else None,
        "duplication_percentage": None,
        "duplicated_lines": sum(m.duplicated_lines or 0 for m in ok) if ok else None,
        **risks,
        "total_loc": project.total_loc,
        "files_analyzed": project.files_analyzed,
        "total_classes": project.total_classes,
        "total_functions": project.total_functions,
        "language_breakdown": language_breakdown(modules),
        "architecture_project_type": architecture_type,
        "fingerprint": build_fingerprint((m.file_path for m in modules), dict(Counter(m.language for m in modules))),
    }


def next_version_number(db: Session, identity_id: int) -> int:
    current = db.query(func.max(ProjectVersion.version_number)).filter(ProjectVersion.identity_id == identity_id).scalar()
    return (current or 0) + 1


def identity_summaries(db: Session) -> list[dict[str, Any]]:
    """Every identity with its latest version's structure, for matching/listing."""
    summaries: list[dict[str, Any]] = []
    for identity in db.query(ProjectIdentity).order_by(ProjectIdentity.id).all():
        versions = list(identity.versions)
        latest = versions[-1] if versions else None
        summaries.append(
            {
                "identity_id": identity.id,
                "name": identity.name,
                "name_keys": list(identity.name_keys or []),
                "version_count": len(versions),
                "latest_version": latest.version_number if latest else 0,
                "latest_scan_id": latest.scan_id if latest else None,
                "fingerprint": (latest.fingerprint if latest else None) or {},
            }
        )
    return summaries


def merge_name_keys(identity: ProjectIdentity, zip_name: str, root_folder: str | None) -> None:
    keys = list(identity.name_keys or [])
    for key in name_keys_for(zip_name, root_folder):
        if key not in keys:
            keys.append(key)
    identity.name_keys = keys
    identity.updated_at = datetime.utcnow()


# --------------------------------------------------------------------------
# Startup migration
# --------------------------------------------------------------------------


def backfill_versions(db: Session) -> int:
    """
    Give every scan stored before Phase 9A a version record.

    Earlier phases defined "the same project" as "the same exact upload name",
    so legacy scans are grouped by exact name (oldest first) into one identity
    per name - preserving the history users already see - and numbered V1, V2...
    Idempotent: only scans without a version are touched, and a later legacy
    scan joins its name's existing identity. All-or-nothing (one transaction).
    Returns the number of versions created.
    """
    missing = (
        db.query(Project)
        .outerjoin(ProjectVersion, ProjectVersion.scan_id == Project.id)
        .filter(ProjectVersion.id.is_(None))
        .order_by(Project.upload_date.asc(), Project.id.asc())
        .all()
    )
    if not missing:
        return 0

    created = 0
    try:
        for project in missing:
            identity = (
                db.query(ProjectIdentity)
                .filter(ProjectIdentity.name == project.name)
                .order_by(ProjectIdentity.id)
                .first()
            )
            if identity is None:
                identity = ProjectIdentity(
                    name=project.name,
                    normalized_name=normalize_name(project.name),
                    name_keys=name_keys_for(project.name, None),
                    created_at=project.upload_date or datetime.utcnow(),
                    updated_at=project.upload_date or datetime.utcnow(),
                )
                db.add(identity)
                db.flush()
            modules = db.query(Module).filter(Module.project_id == project.id).order_by(Module.file_path).all()
            architecture = db.query(ArchitectureScan).filter(ArchitectureScan.project_id == project.id).first()
            snapshot = snapshot_from_stored_scan(
                project, modules, architecture.project_type if architecture is not None else None
            )
            db.add(
                ProjectVersion(
                    identity_id=identity.id,
                    scan_id=project.id,
                    version_number=next_version_number(db, identity.id),
                    created_at=project.upload_date or datetime.utcnow(),
                    resolution="backfilled",
                    resolution_reason="Created by the Phase 9A migration from a scan stored earlier, grouped by exact project name.",
                    is_backfilled=True,
                    **snapshot,
                )
            )
            db.flush()
            created += 1
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Phase 9A version backfill failed; no partial changes were kept")
        raise
    return created


# --------------------------------------------------------------------------
# Read-side helpers
# --------------------------------------------------------------------------


def version_row(version: ProjectVersion) -> dict[str, Any]:
    """One version as the history/versions APIs expose it. `scanned_at` is
    returned as the stored datetime; API layers convert it with `iso_utc`."""
    return {
        "scan_id": version.scan_id,
        "version_number": version.version_number,
        "scanned_at": version.created_at,
        "health_score": version.health_score,
        "health_status": version.health_status,
        "category_scores": version.category_scores or {},
        "metrics": {
            "average_complexity": version.average_complexity,
            "total_complexity": version.total_complexity,
            "average_maintainability": version.average_maintainability,
            "duplication_percentage": version.duplication_percentage,
            "duplicated_lines": version.duplicated_lines,
            "average_risk_score": version.average_risk_score,
            "max_risk_score": version.max_risk_score,
            "high_risk_modules": version.high_risk_modules,
            "risk_summary": version.risk_summary,
            "total_loc": version.total_loc,
            "files_analyzed": version.files_analyzed,
            "total_classes": version.total_classes,
            "total_functions": version.total_functions,
        },
        "languages": version.language_breakdown or {},
        "architecture_project_type": version.architecture_project_type,
        "resolution": version.resolution,
        "is_backfilled": bool(version.is_backfilled),
    }


def ensure_version(db: Session, project: Project) -> ProjectVersion | None:
    """The scan's version, backfilling lazily if the startup migration has not
    covered it (e.g. a row inserted by an older process)."""
    if project.version is None:
        backfill_versions(db)
        db.refresh(project)
    return project.version
