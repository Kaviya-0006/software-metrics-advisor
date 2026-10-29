"""
Tests for POST /api/projects/upload.
"""

import io
import zipfile


def _make_zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_upload_valid_python_zip_returns_analysis(client):
    zip_bytes = _make_zip_bytes({
        "main.py": (
            "class Foo:\n"
            "    def bar(self, x):\n"
            "        if x > 0:\n"
            "            return x\n"
            "        return -x\n"
        ),
    })

    response = client.post(
        "/api/projects/upload",
        files={"file": ("demo.zip", zip_bytes, "application/zip")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["project_id"] > 0
    assert body["project_name"] == "demo"
    assert body["analysis"]["project"]["files"] == 1
    assert body["analysis"]["project"]["classes"] == 1
    assert len(body["analysis"]["modules"]) == 1
    assert body["analysis"]["modules"][0]["file_name"] == "main.py"
    assert body["analysis"]["modules"][0]["complexity"]["total"] == 2


def test_upload_rejects_non_zip_file(client):
    response = client.post(
        "/api/projects/upload",
        files={"file": ("notes.txt", b"just text", "text/plain")},
    )
    assert response.status_code == 400
    assert "zip" in response.json()["detail"].lower()


def test_upload_rejects_zip_with_path_traversal(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("../../evil.py", "x = 1\n")

    response = client.post(
        "/api/projects/upload",
        files={"file": ("evil.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 400


def test_upload_rejects_zip_with_no_supported_source_files(client):
    zip_bytes = _make_zip_bytes({"readme.md": "# nothing to analyze\n"})

    response = client.post(
        "/api/projects/upload",
        files={"file": ("empty.zip", zip_bytes, "application/zip")},
    )
    assert response.status_code == 400
    assert "no supported source files" in response.json()["detail"].lower()


def test_upload_java_and_python_together(client):
    zip_bytes = _make_zip_bytes({
        "src/Foo.java": "public class Foo {\n    public void bar() {}\n}\n",
        "helper.py": "def f():\n    return 1\n",
    })

    response = client.post(
        "/api/projects/upload",
        files={"file": ("mixed.zip", zip_bytes, "application/zip")},
    )

    assert response.status_code == 200
    body = response.json()
    assert sorted(body["analysis"]["project"]["languages"]) == ["java", "python"]
    assert body["analysis"]["project"]["files"] == 2


def test_upload_ignores_configured_directories(client):
    zip_bytes = _make_zip_bytes({
        "main.py": "def f():\n    return 1\n",
        "node_modules/vendored.py": "def should_be_ignored():\n    return 2\n",
    })

    response = client.post(
        "/api/projects/upload",
        files={"file": ("proj.zip", zip_bytes, "application/zip")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["project"]["files"] == 1
    assert body["analysis"]["modules"][0]["file_name"] == "main.py"


def test_upload_response_includes_health_and_risk(client):
    zip_bytes = _make_zip_bytes({
        "good.py": (
            "class Good:\n"
            "    def simple(self):\n"
            "        return 1\n"
        ),
    })

    response = client.post(
        "/api/projects/upload",
        files={"file": ("healthy.zip", zip_bytes, "application/zip")},
    )

    assert response.status_code == 200
    body = response.json()

    assert "health" in body
    assert isinstance(body["health"]["score"], (int, float))
    assert body["health"]["status"] in (
        "CRITICAL", "POOR", "NEEDS_ATTENTION", "HEALTHY", "EXCELLENT",
    )

    assert "category_scores" in body
    assert set(body["category_scores"].keys()) == {
        "maintainability", "complexity", "code_quality", "testing",
        "coupling_cohesion", "duplication", "documentation", "code_churn",
    }
    # Never fabricated — Phase 2 has no coverage/churn data source.
    assert body["category_scores"]["testing"] is None
    assert body["category_scores"]["code_churn"] is None

    assert "risky_modules" in body
    assert isinstance(body["risky_modules"], list)

    module = body["analysis"]["modules"][0]
    assert "risk_score" in module
    assert "risk_level" in module
    assert "risk_factors" in module


def test_upload_low_quality_project_ranks_as_risky(client):
    # Deeply nested branching, high coupling, near-zero documentation.
    bad_code = (
        "import a, b, c, d, e, f, g, h\n\n"
        "class Bad:\n"
        "    def tangled(self, x):\n"
        "        if x > 0:\n"
        "            if x > 1:\n"
        "                if x > 2:\n"
        "                    if x > 3:\n"
        "                        if x > 4:\n"
        "                            return 1\n"
        "        return 0\n"
    )
    zip_bytes = _make_zip_bytes({"bad.py": bad_code})

    response = client.post(
        "/api/projects/upload",
        files={"file": ("bad.zip", zip_bytes, "application/zip")},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["risky_modules"]) == 1
    assert body["risky_modules"][0]["file_name"] == "bad.py"
    assert body["risky_modules"][0]["risk_score"] > 0


def test_health_and_risk_are_persisted_to_database(client):
    from app.database.database import get_db
    from app.database.models import Module, Project
    from app.main import app as fastapi_app

    zip_bytes = _make_zip_bytes({
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    })

    response = client.post(
        "/api/projects/upload",
        files={"file": ("proj.zip", zip_bytes, "application/zip")},
    )
    project_id = response.json()["project_id"]

    override = fastapi_app.dependency_overrides[get_db]
    db = next(override())
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        assert project is not None
        assert project.health_score is not None
        assert project.health_status is not None
        assert project.category_scores is not None

        modules = db.query(Module).filter(Module.project_id == project_id).all()
        assert len(modules) == 1
        assert modules[0].risk_score is not None
        assert modules[0].risk_level is not None
        assert isinstance(modules[0].risk_factors, list)
    finally:
        db.close()
