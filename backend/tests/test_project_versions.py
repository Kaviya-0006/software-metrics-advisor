"""
Phase 9A - repeated uploads, project separation, immutable version history and
the history API, through the real upload pipeline and SQLite.
"""

import io
import zipfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base, get_db
from app.database.models import (
    ArchitectureScan,
    Module,
    Project,
    ProjectIdentity,
    ProjectVersion,
)
from app.database.versioning import backfill_versions
from app.main import app

PY = "def f():\n    return 1\n"
V1 = {"main.py": PY, "util.py": "def g():\n    return 2\n"}
V2 = {**V1, "more.py": "def h(x):\n    if x:\n        return 3\n    return 4\n"}
V3 = {**V2, "extra.py": "class A:\n    def m(self):\n        return 5\n"}


def _zip(files, root=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(f"{root}/{name}" if root else name, content)
    return buffer.getvalue()


def _upload(client, files, name="proj.zip", root=None, expect=200, **data):
    response = client.post(
        "/api/projects/upload",
        files={"file": (name, _zip(files, root), "application/zip")},
        data={k: str(v) for k, v in data.items()},
    )
    assert response.status_code == expect, response.text
    return response.json()


def _history(client, project_id):
    response = client.get(f"/api/projects/{project_id}/history")
    assert response.status_code == 200, response.text
    return response.json()


def _session():
    generator = app.dependency_overrides[get_db]()
    return next(generator), generator


def _counts():
    session, generator = _session()
    try:
        return tuple(
            session.query(model).count()
            for model in (Project, Module, ArchitectureScan, ProjectIdentity, ProjectVersion)
        )
    finally:
        generator.close()


# ------------------------------------------------------- repeated uploads


def test_first_upload_creates_project_with_version_one(client):
    uploaded = _upload(client, V1)
    version = uploaded["version"]
    assert (version["version_number"], version["is_new_project"], version["resolution"]) == (1, True, "new")

    history = _history(client, uploaded["project_id"])
    assert history["current_version"] == 1
    assert [h["version_number"] for h in history["history"]] == [1]
    entry = history["history"][0]
    assert entry["scan_id"] == uploaded["project_id"]
    assert entry["health_score"] == uploaded["health"]["score"]


def test_second_and_third_upload_become_v2_and_v3_of_the_same_project(client):
    first = _upload(client, V1)
    second = _upload(client, V2)
    third = _upload(client, V3)

    identity_ids = {first["version"]["identity_id"], second["version"]["identity_id"], third["version"]["identity_id"]}
    assert len(identity_ids) == 1
    assert [u["version"]["version_number"] for u in (first, second, third)] == [1, 2, 3]
    assert second["version"]["resolution"] == "auto-matched"

    history = _history(client, third["project_id"])["history"]
    assert [h["version_number"] for h in history] == [1, 2, 3]
    assert [h["scan_id"] for h in history] == [first["project_id"], second["project_id"], third["project_id"]]
    assert [h["metrics"]["files_analyzed"] for h in history] == [2, 3, 4]
    # Any version's history is the whole project's history.
    assert _history(client, first["project_id"])["history"] == history


def test_version_snapshot_stores_every_required_metric(client):
    uploaded = _upload(client, V3)
    entry = _history(client, uploaded["project_id"])["history"][0]
    metrics = entry["metrics"]
    project = uploaded["analysis"]["project"]
    summary = uploaded["analysis"]["summary"]

    # Each stored number equals what that upload's own analysis reported.
    assert entry["health_score"] == uploaded["health"]["score"]
    assert entry["health_status"] == uploaded["health"]["status"]
    assert entry["category_scores"] == uploaded["category_scores"]
    assert metrics["average_complexity"] == summary["average_complexity"]
    assert metrics["total_complexity"] == summary["total_complexity"]
    assert metrics["average_maintainability"] == summary["average_maintainability"]
    assert metrics["duplication_percentage"] == summary["duplication_percentage"]
    assert metrics["duplicated_lines"] == summary["duplicated_lines"]
    assert metrics["total_loc"] == summary["total_loc"]
    assert metrics["files_analyzed"] == project["files"] == 4
    assert metrics["total_classes"] == project["classes"]
    assert metrics["total_functions"] == project["functions"]
    scores = [m["risk_score"] for m in uploaded["analysis"]["modules"] if m["risk_score"] is not None]
    assert metrics["average_risk_score"] == round(sum(scores) / len(scores), 2)
    assert metrics["max_risk_score"] == round(max(scores), 2)
    assert entry["languages"]["python"]["files"] == 4
    assert entry["languages"]["python"]["loc"] == summary["total_loc"]
    assert set(metrics["risk_summary"]) == {"low", "medium", "high", "critical"}
    assert sum(metrics["risk_summary"].values()) == 4
    assert entry["scanned_at"].endswith("Z")
    datetime.fromisoformat(entry["scanned_at"].replace("Z", "+00:00"))


def test_previous_versions_are_preserved_unchanged(client):
    first = _upload(client, V1)
    before_detail = client.get(f"/api/projects/{first['project_id']}").json()
    before_risks = client.get(f"/api/projects/{first['project_id']}/risks").json()
    before_arch = client.get(f"/api/projects/{first['project_id']}/architecture").json()
    before_row = _history(client, first["project_id"])["history"][0]

    _upload(client, V2)
    _upload(client, V3)

    assert client.get(f"/api/projects/{first['project_id']}").json() == before_detail
    assert client.get(f"/api/projects/{first['project_id']}/risks").json() == before_risks
    assert client.get(f"/api/projects/{first['project_id']}/architecture").json() == before_arch
    assert _history(client, first["project_id"])["history"][0] == before_row


def test_history_scan_ids_are_resolvable_and_each_has_its_own_architecture(client):
    _upload(client, V1)
    last = _upload(client, V2)
    for row in _history(client, last["project_id"])["history"]:
        arch = client.get(f"/api/projects/{row['scan_id']}/architecture").json()
        assert arch["available"] is True
        assert arch["architecture"]["summary"]["total_files"] == row["metrics"]["files_analyzed"]


def test_versions_are_immutable(client):
    uploaded = _upload(client, V1)
    session, generator = _session()
    try:
        version = session.query(ProjectVersion).filter_by(scan_id=uploaded["project_id"]).one()
        version.health_score = 1.0
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()
    finally:
        generator.close()
    assert _history(client, uploaded["project_id"])["history"][0]["health_score"] == uploaded["health"]["score"]


# --------------------------------------------------- project separation


def test_same_filename_but_unrelated_structure_is_a_separate_project(client):
    a = _upload(client, V1, "inventory.zip")
    b = _upload(client, {"Server.java": "public class Server {\n    int one() { return 1; }\n}\n"}, "inventory.zip")
    assert a["version"]["identity_id"] != b["version"]["identity_id"]
    assert b["version"]["is_new_project"] is True
    assert len(_history(client, a["project_id"])["history"]) == 1
    assert len(_history(client, b["project_id"])["history"]) == 1


def test_different_filename_with_identical_structure_is_not_merged_but_suggested(client):
    a = _upload(client, V2, "alpha.zip")
    b = _upload(client, V2, "beta.zip")
    assert a["version"]["identity_id"] != b["version"]["identity_id"]
    assert [s["identity_id"] for s in b["version"]["suggestions"]] == [a["version"]["identity_id"]]
    assert b["version"]["suggestions"][0]["auto_match_eligible"] is False


def test_generic_zip_names_are_never_matched_by_name(client):
    a = _upload(client, V1, "download.zip")
    b = _upload(client, V1, "download.zip")
    assert a["version"]["identity_id"] != b["version"]["identity_id"]


def test_zip_root_folder_renaming_keeps_project_identity(client):
    first = _upload(client, V1, "inventory.zip", root="inventory-v1")
    second = _upload(client, V2, "inventory-v2.zip", root="inventory-v2")
    assert second["version"]["identity_id"] == first["version"]["identity_id"]
    assert second["version"]["version_number"] == 2


def test_root_folder_name_alone_can_identify_a_generically_named_zip(client):
    first = _upload(client, V1, "myapp.zip", root="myapp-main")
    second = _upload(client, V2, "download.zip", root="myapp-master")
    assert second["version"]["identity_id"] == first["version"]["identity_id"]


# --------------------------------------------------- explicit selection


def test_explicit_existing_project_adds_a_version_regardless_of_name(client):
    first = _upload(client, V1, "alpha.zip")
    second = _upload(client, V2, "totally-different.zip", identity_id=first["version"]["identity_id"])
    assert second["version"]["identity_id"] == first["version"]["identity_id"]
    assert second["version"]["resolution"] == "explicit"
    assert second["project_name"] == first["project_name"]  # stable project name


def test_explicit_selection_of_a_dissimilar_project_warns_but_is_honored(client):
    first = _upload(client, V1, "alpha.zip")
    second = _upload(
        client,
        {"Main.java": "public class Main {\n    int one() { return 1; }\n}\n"},
        "alpha.zip",
        identity_id=first["version"]["identity_id"],
    )
    assert second["version"]["version_number"] == 2
    assert second["version"]["warnings"]


def test_create_new_forces_a_new_project_and_accepts_a_name(client):
    first = _upload(client, V1)
    second = _upload(client, V2, "proj.zip", create_new="true", project_name="Brand New")
    assert second["version"]["identity_id"] != first["version"]["identity_id"]
    assert second["project_name"] == "Brand New"
    assert second["version"]["resolution"] == "new"


def test_conflicting_or_invalid_project_options_are_rejected_and_save_nothing(client):
    first = _upload(client, V1)
    before = _counts()
    _upload(client, V2, expect=400, identity_id=first["version"]["identity_id"], create_new="true")
    _upload(client, V2, expect=404, identity_id=99999)
    assert _counts() == before


# --------------------------------------------- failed uploads leave nothing


def test_failed_uploads_leave_no_partial_snapshot(client):
    _upload(client, V1)
    before = _counts()

    not_zip = client.post("/api/projects/upload", files={"file": ("x.txt", b"hi", "text/plain")})
    assert not_zip.status_code == 400
    no_source = client.post(
        "/api/projects/upload",
        files={"file": ("empty.zip", _zip({"README.md": "# nothing"}), "application/zip")},
    )
    assert no_source.status_code == 400
    corrupt = client.post("/api/projects/upload", files={"file": ("bad.zip", b"not a zip", "application/zip")})
    assert corrupt.status_code == 400

    assert _counts() == before


def test_a_failure_while_saving_rolls_back_scan_modules_architecture_and_version(client, monkeypatch):
    import app.api.upload as upload_module

    _upload(client, V1)
    before = _counts()

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure while placing the version")

    monkeypatch.setattr(upload_module, "next_version_number", boom)
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as lenient:
        response = lenient.post(
            "/api/projects/upload",
            files={"file": ("proj.zip", _zip(V2), "application/zip")},
        )
    assert response.status_code == 500
    assert _counts() == before


# ------------------------------------------------------ mixed languages


def test_typescript_frontend_plus_java_backend_versions(client):
    backend = {"backend/src/Api.java": "public class Api {\n    public int one() { return 1; }\n}\n"}
    frontend = {
        "frontend/src/api.ts": "export const load = () => fetch('/api/users');\n",
        "frontend/src/App.tsx": "export const App = () => null;\n",
    }
    first = _upload(client, {**backend, **frontend}, "shop.zip")
    second = _upload(
        client,
        {**backend, **frontend, "backend/src/Extra.java": "public class Extra {\n    int two() { return 2; }\n}\n"},
        "shop.zip",
    )
    assert second["version"]["identity_id"] == first["version"]["identity_id"]

    history = _history(client, second["project_id"])["history"]
    assert [h["version_number"] for h in history] == [1, 2]
    assert history[0]["languages"]["java"]["files"] == 1
    assert history[1]["languages"]["java"]["files"] == 2
    assert history[1]["languages"]["typescript"]["files"] == 2
    for row in history:
        assert client.get(f"/api/projects/{row['scan_id']}/architecture").json()["available"] is True


# --------------------------------------------------- suggest-match / lists


def test_suggest_match_reports_candidates_without_saving(client):
    first = _upload(client, V1)
    before = _counts()
    response = client.post(
        "/api/projects/suggest-match",
        files={"file": ("proj-v2.zip", _zip(V2), "application/zip")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["auto_match_identity_id"] == first["version"]["identity_id"]
    assert body["suggestions"][0]["reasons"]
    assert _counts() == before


def test_suggest_match_rejects_non_zip_and_empty_projects(client):
    assert client.post("/api/projects/suggest-match", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 400
    empty = client.post(
        "/api/projects/suggest-match",
        files={"file": ("e.zip", _zip({"README.md": "x"}), "application/zip")},
    )
    assert empty.status_code == 400


def test_identities_list_and_detail(client):
    first = _upload(client, V1, "alpha.zip")
    _upload(client, V2, "alpha.zip")
    other = _upload(client, {"Main.java": "public class Main {\n    int one() { return 1; }\n}\n"}, "beta.zip")

    listing = client.get("/api/identities").json()["identities"]
    by_id = {i["identity_id"]: i for i in listing}
    assert len(listing) == 2
    assert by_id[first["version"]["identity_id"]]["version_count"] == 2
    assert by_id[first["version"]["identity_id"]]["latest_version"] == 2
    assert by_id[other["version"]["identity_id"]]["version_count"] == 1

    detail = client.get(f"/api/identities/{first['version']['identity_id']}").json()
    assert [v["version_number"] for v in detail["versions"]] == [1, 2]
    assert client.get("/api/identities/99999").status_code == 404


def test_project_list_exposes_identity_and_version(client):
    uploaded = _upload(client, V1)
    entry = client.get("/api/projects").json()["projects"][0]
    assert entry["identity_id"] == uploaded["version"]["identity_id"]
    assert entry["version_number"] == 1


# ------------------------------------------------------------ migration


def test_backfill_groups_legacy_scans_by_exact_name_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        base = datetime(2026, 1, 1)
        for index, name in enumerate(["alpha", "alpha", "beta"]):
            project = Project(
                name=name, status="analyzed", files_analyzed=1, total_loc=10, total_classes=0,
                total_functions=1, health_score=50.0 + index, health_status="FAIR",
                category_scores={"complexity": 40.0}, upload_date=base + timedelta(days=index),
            )
            session.add(project)
            session.flush()
            session.add(
                Module(
                    project_id=project.id, file_name="a.py", file_path="a.py", language="python",
                    loc_total=10, complexity_total=4, complexity_average=2.0,
                    maintainability_index=70.0, duplicated_lines=0, risk_score=40.0, risk_level="MEDIUM",
                )
            )
        session.commit()

        assert backfill_versions(session) == 3
        assert backfill_versions(session) == 0  # idempotent

        identities = {i.name: i for i in session.query(ProjectIdentity).all()}
        assert [v.version_number for v in identities["alpha"].versions] == [1, 2]
        assert [v.version_number for v in identities["beta"].versions] == [1]
        v1 = identities["alpha"].versions[0]
        assert v1.is_backfilled is True and v1.resolution == "backfilled"
        assert v1.average_complexity == 2.0 and v1.average_maintainability == 70.0
        assert v1.duplication_percentage is None  # never stored per scan: not invented
        assert v1.language_breakdown == {"python": {"files": 1, "loc": 10}}
        assert v1.risk_summary["medium"] == 1
    finally:
        session.close()
