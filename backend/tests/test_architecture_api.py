"""
Phase 8D — persistence, API retrieval and upload integration.

Everything goes through the real upload pipeline (ZIP -> scanner -> parser ->
metrics -> 8A-8C analyzers -> SQLite) and the real HTTP routes.
"""

import io
import zipfile
from datetime import datetime

from app.database.database import get_db
from app.database.models import ArchitectureScan, Module, Project
from app.main import app

FASTAPI_BACKEND = (
    "from fastapi import FastAPI\n"
    "app = FastAPI()\n"
    "@app.get('/api/users')\n"
    "def users():\n"
    "    return []\n"
    "@app.delete('/api/users/{id}')\n"
    "def remove(id):\n"
    "    return 1\n"
)
JS_FRONTEND = (
    "export function load() { return fetch('/api/users'); }\n"
    "export function broken() { return fetch('/api/missing', { method: 'POST' }); }\n"
)
FULL_STACK = {"backend/main.py": FASTAPI_BACKEND, "frontend/src/api.js": JS_FRONTEND}


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _upload(client, files, name="proj.zip"):
    response = client.post(
        "/api/projects/upload",
        files={"file": (name, _zip_bytes(files), "application/zip")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _db_session():
    """A session on the same throwaway database the `client` fixture uses."""
    generator = app.dependency_overrides[get_db]()
    session = next(generator)
    return session, generator


def _architecture(client, project_id):
    response = client.get(f"/api/projects/{project_id}/architecture")
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------- persistence


def test_upload_persists_architecture_for_the_scan(client):
    uploaded = _upload(client, FULL_STACK)
    session, generator = _db_session()
    try:
        scans = session.query(ArchitectureScan).all()
        assert len(scans) == 1
        scan = scans[0]
        assert scan.project_id == uploaded["project_id"]
        assert scan.status == "analyzed"
        assert scan.project_type == "full-stack"
        assert (scan.matched_count, scan.unmatched_calls_count, scan.unused_endpoints_count) == (1, 1, 1)
        assert scan.report_json["api_mapping"]["matched"] == 1
    finally:
        generator.close()


def test_upload_response_carries_architecture_headline(client):
    uploaded = _upload(client, FULL_STACK)
    assert uploaded["architecture"]["project_type"] == "full-stack"
    assert uploaded["architecture"]["matched"] == 1
    assert uploaded["architecture"]["detected_layers"] == ["frontend", "backend"]


# ----------------------------------------------------------------- retrieval


def test_get_architecture_returns_saved_scan(client):
    uploaded = _upload(client, FULL_STACK)
    body = _architecture(client, uploaded["project_id"])

    assert body["available"] is True
    assert body["project_id"] == body["scan_id"] == uploaded["project_id"]
    assert body["project_name"] == "proj"
    datetime.fromisoformat(body["scanned_at"])

    architecture = body["architecture"]
    assert architecture["project_type"] == "full-stack"
    layers = {layer["layer"]: layer for layer in architecture["layers"]}
    assert layers["frontend"]["languages"] == {"javascript": 1}
    assert layers["backend"]["languages"] == {"python": 1}

    mapping = architecture["api_mapping"]
    assert len(mapping["matched_items"]) == 1
    assert len(mapping["unmatched_frontend_calls_items"]) == 1
    assert len(mapping["unused_backend_endpoints_items"]) == 1
    unmatched = mapping["unmatched_frontend_calls_items"][0]
    assert unmatched["evidence"][0]["file"] == "frontend/src/api.js"
    assert unmatched["evidence"][0]["line"] == 2
    assert "Potential integration issue" in unmatched["reason"]
    unused = mapping["unused_backend_endpoints_items"][0]
    assert unused["evidence"][0]["file"] == "backend/main.py"
    assert "Potentially unused" in unused["reason"]
    assert architecture["diagram"]["edges"][0]["count"] == 1


def test_each_scan_keeps_its_own_architecture(client):
    first = _upload(client, FULL_STACK, "same.zip")
    second = _upload(client, {"backend/main.py": FASTAPI_BACKEND}, "same.zip")  # same name, backend only
    assert first["project_id"] != second["project_id"]

    a = _architecture(client, first["project_id"])["architecture"]
    b = _architecture(client, second["project_id"])["architecture"]
    assert a["project_type"] == "full-stack"
    assert b["project_type"] == "backend-only"
    # Re-uploading must not have altered the older scan's result.
    assert _architecture(client, first["project_id"])["architecture"] == a


def test_get_architecture_is_a_pure_read(client):
    uploaded = _upload(client, FULL_STACK)
    first = _architecture(client, uploaded["project_id"])
    second = _architecture(client, uploaded["project_id"])
    assert first == second


def test_architecture_404_for_unknown_project(client):
    response = client.get("/api/projects/9999/architecture")
    assert response.status_code == 404
    assert list(response.json().keys()) == ["detail"]


def test_scan_without_saved_architecture_reports_unavailable(client):
    """A scan stored before Phase 8D has no architecture row: say so, never invent one."""
    session, generator = _db_session()
    try:
        project = Project(name="legacy", status="analyzed", files_analyzed=1)
        session.add(project)
        session.commit()
        project_id = project.id
    finally:
        generator.close()

    body = _architecture(client, project_id)
    assert body["available"] is False
    assert body["architecture"] is None
    assert "Re-upload" in body["message"]


def test_failed_architecture_step_does_not_break_the_scan(client, monkeypatch):
    import app.api.upload as upload_module

    def boom(*args, **kwargs):
        raise RuntimeError("simulated analyzer failure")

    monkeypatch.setattr(upload_module, "build_architecture_report", boom)
    uploaded = _upload(client, FULL_STACK)
    assert uploaded["success"] is True
    assert uploaded["architecture"] is None

    body = _architecture(client, uploaded["project_id"])
    assert body["available"] is False
    assert body["status"] == "failed"
    assert "simulated" not in body["message"]  # internals never leak to the client
    # Metrics for the same scan are intact.
    assert client.get(f"/api/projects/{uploaded['project_id']}/metrics").status_code == 200


# -------------------------------------------------------- project shapes


def test_frontend_only_zip(client):
    uploaded = _upload(client, {"src/api.js": "fetch('/api/a')\n", "src/ui.jsx": "export const A = () => null;\n"})
    architecture = _architecture(client, uploaded["project_id"])["architecture"]
    assert architecture["project_type"] == "frontend-only"
    assert architecture["api_mapping"]["unmatched_frontend_calls"] == 0
    assert architecture["api_mapping"]["uncertain"] == 1


def test_backend_only_zip(client):
    uploaded = _upload(client, {"backend/main.py": FASTAPI_BACKEND})
    architecture = _architecture(client, uploaded["project_id"])["architecture"]
    assert architecture["project_type"] == "backend-only"
    assert architecture["api_mapping"]["total_endpoints"] == 2
    assert architecture["diagram"]["edges"] == []


def test_mixed_language_zip(client):
    uploaded = _upload(
        client,
        {
            "backend/main.py": FASTAPI_BACKEND,
            "backend/Helper.java": "public class Helper {\n    public int one() { return 1; }\n}\n",
            "frontend/src/api.ts": "export const load = () => fetch('/api/users');\n",
            "frontend/src/App.tsx": "export const App = () => null;\n",
        },
    )
    architecture = _architecture(client, uploaded["project_id"])["architecture"]
    layers = {layer["layer"]: layer for layer in architecture["layers"]}
    assert layers["backend"]["languages"] == {"java": 1, "python": 1}
    assert layers["frontend"]["languages"] == {"typescript": 2}
    assert architecture["api_mapping"]["matched"] == 1


def test_demo_projects_all_produce_an_architecture(client):
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent / "sample_projects"
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        files = {
            str(path.relative_to(directory)): path.read_text()
            for path in directory.rglob("*")
            if path.is_file()
        }
        uploaded = _upload(client, files, f"{directory.name}.zip")
        body = _architecture(client, uploaded["project_id"])
        assert body["available"] is True, directory.name
        total = sum(layer["files"] for layer in body["architecture"]["layers"])
        assert total == uploaded["analysis"]["project"]["files"], directory.name


# ----------------------------------------------- existing features preserved


def test_existing_endpoints_unaffected(client):
    uploaded = _upload(client, FULL_STACK)
    project_id = uploaded["project_id"]
    for suffix in ("", "/metrics", "/risks", "/history", "/report"):
        assert client.get(f"/api/projects/{project_id}{suffix}").status_code == 200, suffix
    assert client.get("/api/projects").status_code == 200
    session, generator = _db_session()
    try:
        assert session.query(Module).filter(Module.project_id == project_id).count() == 2
    finally:
        generator.close()
