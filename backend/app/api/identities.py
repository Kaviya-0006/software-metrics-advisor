"""
Project identities and their versions (Phase 9A).

    GET /api/identities
    GET /api/identities/{identity_id}
    GET /api/identities/{identity_id}/compare   (Phase 9B)

An identity is the stable "project"; each of its versions is one immutable
upload (a scan snapshot, addressed everywhere else by its `scan_id` /
`project_id`). Pure reads of what the upload stored.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.analyzer.comparison import compare_versions
from app.database.database import get_db
from app.database.models import (
    ArchitectureScan,
    FileFingerprint,
    Module,
    ProjectIdentity,
    ProjectVersion,
    Project,
)
from app.database.schemas import (
    IdentityDetailResponse,
    IdentityListResponse,
    VersionComparisonResponse,
)
from app.database.versioning import iso_utc, version_row

router = APIRouter(prefix="/api/identities", tags=["identities"])


def _identity_dict(identity: ProjectIdentity) -> dict:
    versions = list(identity.versions)
    latest = versions[-1] if versions else None
    return {
        "identity_id": identity.id,
        "name": identity.name,
        "created_at": iso_utc(identity.created_at),
        "updated_at": iso_utc(identity.updated_at),
        "version_count": len(versions),
        "latest_version": latest.version_number if latest else None,
        "latest_scan_id": latest.scan_id if latest else None,
        "latest_health_score": latest.health_score if latest else None,
        "latest_health_status": latest.health_status if latest else None,
        "languages": list((latest.language_breakdown or {}).keys()) if latest else [],
    }


@router.get(
    "",
    response_model=IdentityListResponse,
    summary="List projects (stable identities) with their latest version",
    description=(
        "One entry per project, most recently updated first, with the number of "
        "versions and the scan id of the latest one. Use `latest_scan_id` as the "
        "`project_id` of the other endpoints."
    ),
)
def list_identities(db: Session = Depends(get_db)):
    identities = db.query(ProjectIdentity).all()
    entries = [_identity_dict(i) for i in identities if i.versions]
    entries.sort(key=lambda e: (e["updated_at"] or "", e["identity_id"]), reverse=True)
    return IdentityListResponse(identities=entries)


@router.get(
    "/{identity_id}",
    response_model=IdentityDetailResponse,
    summary="Get one project with all of its versions",
    responses={404: {"description": "Project not found."}},
)
def get_identity(identity_id: int, db: Session = Depends(get_db)):
    identity = db.get(ProjectIdentity, identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail=f"Project {identity_id} was not found.")
    versions = []
    for v in identity.versions:
        row = version_row(v)
        row["scanned_at"] = iso_utc(row["scanned_at"])
        versions.append(row)
    return IdentityDetailResponse(identity=_identity_dict(identity), versions=versions)


@router.delete(
    "/{identity_id}",
    status_code=204,
    summary="Delete a project and all of its versions",
    responses={404: {"description": "Project not found."}},
)
def delete_identity(identity_id: int, db: Session = Depends(get_db)):
    """Permanently delete a logical project, every saved version/scan, and
    all scan-owned modules, architecture results, and file fingerprints.

    This is intentionally scoped to one stable project identity. Other
    projects and their versions are not touched.
    """
    identity = db.get(ProjectIdentity, identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail=f"Project {identity_id} was not found.")

    scan_ids = [version.scan_id for version in identity.versions]

    # Delete each scan through the ORM so Project's delete-orphan cascades
    # remove its modules, architecture result, version row, and fingerprints.
    for scan_id in scan_ids:
        scan = db.get(Project, scan_id)
        if scan is not None:
            db.delete(scan)

    # The identity owns the immutable version records. Deleting it also
    # removes any version rows that may not have had a corresponding scan.
    db.delete(identity)
    db.commit()
    return Response(status_code=204)


_MODULE_FIELDS = (
    "file_path", "language", "loc_total", "loc_code", "loc_comments", "loc_blank",
    "classes", "functions", "complexity_total", "complexity_average",
    "complexity_maximum", "maintainability_index", "coupling", "cohesion",
    "depth_of_inheritance", "duplication_percentage", "duplicated_lines",
    "risk_score", "risk_level", "parse_error",
)


def _load_version_data(db: Session, version: ProjectVersion) -> dict:
    """Everything the comparison engine needs about one stored version - all
    read from what that upload saved (nothing is recomputed)."""
    modules = (
        db.query(Module).filter(Module.project_id == version.scan_id).order_by(Module.file_path).all()
    )
    fingerprints = {
        f.file_path: f.sha256
        for f in db.query(FileFingerprint).filter(FileFingerprint.scan_id == version.scan_id).all()
    }
    architecture = (
        db.query(ArchitectureScan).filter(ArchitectureScan.project_id == version.scan_id).first()
    )
    row = version_row(version)
    row["scanned_at"] = iso_utc(row["scanned_at"])
    return {
        "version": row,
        "modules": [{field: getattr(m, field) for field in _MODULE_FIELDS} for m in modules],
        "fingerprints": fingerprints,
        "architecture": architecture.report_json
        if architecture is not None and architecture.status == "analyzed"
        else None,
    }


def _version_header(version: ProjectVersion) -> dict:
    return {
        "version_number": version.version_number,
        "scan_id": version.scan_id,
        "scanned_at": iso_utc(version.created_at),
        "health_score": version.health_score,
        "health_status": version.health_status,
    }


@router.get(
    "/{identity_id}/compare",
    response_model=VersionComparisonResponse,
    summary="Compare two saved versions of the same project",
    description=(
        "Phase 9B. Compares two versions of one project using only what those "
        "uploads stored: project-level metric cards (previous / current / "
        "absolute / percent change for health, complexity, maintainability, "
        "duplication, LOC and file counts, documentation, risk distribution), a "
        "per-file comparison (added / removed / changed / unchanged / "
        "unverified, with per-file complexity, maintainability, coupling, cohesion, "
        "duplication and risk changes), deterministic regression and improvement "
        "alerts with the published threshold rules, architecture/API-integration "
        "changes where both versions saved an architecture analysis, and an explicit "
        "uncertainty list (renames, missing data, ZIP root changes). Files are "
        "matched by normalized relative path, ignoring a ZIP root folder that "
        "changed between uploads. Without parameters it compares the latest "
        "version with its immediate predecessor; `from`/`to` are version numbers "
        "(reordered oldest -> newest if given the other way round). A project "
        "with a single version returns 200 with `available: false`. "
        "404: unknown project or version; 400: identical versions or no earlier version."
    ),
    responses={
        400: {"description": "Same version twice, or no earlier version exists to compare against."},
        404: {"description": "Project or version not found."},
    },
)
def compare_identity_versions(
    identity_id: int,
    from_version: int | None = Query(None, alias="from", description="Version number of the earlier version."),
    to_version: int | None = Query(None, alias="to", description="Version number of the later version (default: latest)."),
    db: Session = Depends(get_db),
):
    identity = db.get(ProjectIdentity, identity_id)
    if identity is None:
        raise HTTPException(status_code=404, detail=f"Project {identity_id} was not found.")
    versions = list(identity.versions)
    headers = [_version_header(v) for v in versions]

    if len(versions) < 2:
        return VersionComparisonResponse(
            identity_id=identity.id,
            project_name=identity.name,
            available=False,
            reason=(
                "This is the first version of the project, so there is nothing to compare yet. "
                "Upload another version (choose this project in the upload panel) to compare."
            ),
            versions=headers,
        )

    by_number = {v.version_number: v for v in versions}
    numbers = sorted(by_number)

    def need(number: int) -> ProjectVersion:
        if number not in by_number:
            raise HTTPException(status_code=404, detail=f"Version {number} of this project was not found.")
        return by_number[number]

    if to_version is None and from_version is None:
        to_version = numbers[-1]
        from_version = numbers[-2]
    elif from_version is None:
        need(to_version)
        earlier = [n for n in numbers if n < to_version]
        if not earlier:
            raise HTTPException(status_code=400, detail=f"Version {to_version} is the first version; there is no earlier version to compare it with.")
        from_version = earlier[-1]
    elif to_version is None:
        need(from_version)
        to_version = numbers[-1]
        if from_version == to_version:
            raise HTTPException(status_code=400, detail=f"Version {from_version} is already the latest; choose an earlier version or pass `to`.")

    first, second = need(from_version), need(to_version)
    if first.version_number == second.version_number:
        raise HTTPException(status_code=400, detail="Choose two different versions to compare.")
    swapped = first.version_number > second.version_number
    if swapped:
        first, second = second, first

    comparison = compare_versions(_load_version_data(db, first), _load_version_data(db, second))
    return VersionComparisonResponse(
        identity_id=identity.id,
        project_name=identity.name,
        available=True,
        swapped=swapped,
        versions=headers,
        comparison=comparison,
    )
