"""
End-to-end Phase 3 integration tests against the real sample projects
(sample_projects/demo_python, sample_projects/demo_java) — not synthetic
snippets. Verifies the full pipeline: ZIP -> extraction -> parsing ->
Phase 2 metrics -> Phase 3 health scoring -> risk detection -> database
persistence -> JSON response, for real, varied, multi-file source code.
"""

import io
import zipfile
from pathlib import Path

SAMPLE_PROJECTS_ROOT = Path(__file__).resolve().parent.parent.parent / "sample_projects"


def _zip_directory(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(directory)))
    return buffer.getvalue()


def _upload_sample_project(client, directory_name: str, zip_filename: str):
    directory = SAMPLE_PROJECTS_ROOT / directory_name
    zip_bytes = _zip_directory(directory)
    return client.post(
        "/api/projects/upload",
        files={"file": (zip_filename, zip_bytes, "application/zip")},
    )


def _assert_well_formed_health_response(body: dict):
    assert body["success"] is True

    # Health score + status.
    assert "score" in body["health"]
    assert "status" in body["health"]
    assert 0.0 <= body["health"]["score"] <= 100.0
    assert body["health"]["status"] in (
        "CRITICAL", "POOR", "NEEDS_ATTENTION", "HEALTHY", "EXCELLENT",
    )

    # Category scores: all 8 keys present; Testing/Code Churn never
    # fabricated since Phase 2 has no data source for either.
    categories = body["category_scores"]
    assert set(categories.keys()) == {
        "maintainability", "complexity", "code_quality", "testing",
        "coupling_cohesion", "duplication", "documentation", "code_churn",
    }
    assert categories["testing"] is None
    assert categories["code_churn"] is None
    for key in ("maintainability", "complexity", "code_quality",
                "coupling_cohesion", "duplication", "documentation"):
        assert categories[key] is None or 0.0 <= categories[key] <= 100.0

    # Risky-module ranking: sorted descending, every entry has a level.
    risky = body["risky_modules"]
    scores = [m["risk_score"] for m in risky]
    assert scores == sorted(scores, reverse=True)
    for m in risky:
        assert m["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    # Per-module risk fields embedded in analysis.modules.
    for module in body["analysis"]["modules"]:
        assert "risk_score" in module
        assert "risk_level" in module
        assert "risk_factors" in module
        if module["parse_error"] is None:
            assert module["risk_score"] is not None
            assert module["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_demo_python_project_end_to_end(client):
    response = _upload_sample_project(client, "demo_python", "demo_python.zip")
    assert response.status_code == 200
    body = response.json()

    assert body["analysis"]["project"]["files"] == 3
    assert body["analysis"]["project"]["languages"] == ["python"]
    _assert_well_formed_health_response(body)

    # This sample project is clean, well-factored code -> expect a decent
    # health score, not a crash and not a fabricated perfect 100.
    assert body["health"]["score"] > 50


def test_demo_java_project_end_to_end(client):
    response = _upload_sample_project(client, "demo_java", "demo_java.zip")
    assert response.status_code == 200
    body = response.json()

    assert body["analysis"]["project"]["files"] == 3
    assert body["analysis"]["project"]["languages"] == ["java"]
    _assert_well_formed_health_response(body)

    # PaymentService.java has a 3-level inheritance chain and real
    # branching (switch + nested ifs across the chain) — it should show up
    # with a real (non-null) risk score, even if not necessarily the
    # single highest.
    payment_service = next(
        m for m in body["analysis"]["modules"] if m["file_name"] == "PaymentService.java"
    )
    assert payment_service["risk_score"] is not None
    assert payment_service["depth_of_inheritance"] == 2.0


def test_demo_projects_persist_health_and_risk_to_database(client):
    from app.database.database import get_db
    from app.database.models import Module, Project
    from app.main import app as fastapi_app

    response = _upload_sample_project(client, "demo_python", "demo_python.zip")
    project_id = response.json()["project_id"]

    override = fastapi_app.dependency_overrides[get_db]
    db = next(override())
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        assert project.health_score is not None
        assert project.health_status is not None
        assert project.category_scores is not None

        modules = db.query(Module).filter(Module.project_id == project_id).all()
        assert len(modules) == 3
        assert all(m.risk_score is not None for m in modules)
        assert all(m.risk_level is not None for m in modules)
    finally:
        db.close()
