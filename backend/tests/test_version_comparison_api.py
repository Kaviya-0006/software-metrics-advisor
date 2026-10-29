"""
Phase 9B - version comparison through the real pipeline: ZIP uploads with
controlled, known differences -> analysis -> SQLite -> GET /compare.
"""

import hashlib
import io
import zipfile

from app.database.database import get_db
from app.database.models import FileFingerprint, ProjectVersion
from app.main import app

STABLE = "def stable(a, b):\n    return a + b\n"
REMOVED = "def going_away():\n    return 'bye'\n"
ADDED = "def brand_new():\n    return 'hi'\n"


def _branchy(n: int) -> str:
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(n))
    return f"def hot(x):\n{body}    return -1\n"


V1 = {"app/stable.py": STABLE, "app/hot.py": _branchy(1), "app/removed.py": REMOVED}
V2 = {"app/stable.py": STABLE, "app/hot.py": _branchy(14), "app/added.py": ADDED}
V3 = {**V2, "app/another.py": "def another():\n    return 3\n"}


def _zip(files, root=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(f"{root}/{name}" if root else name, content)
    return buffer.getvalue()


def _upload(client, files, name="shop.zip", root=None, **data):
    response = client.post(
        "/api/projects/upload",
        files={"file": (name, _zip(files, root), "application/zip")},
        data={k: str(v) for k, v in data.items()},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _compare(client, identity_id, expect=200, **params):
    response = client.get(f"/api/identities/{identity_id}/compare", params=params)
    assert response.status_code == expect, response.text
    return response.json()


def _rows(body):
    return {row["path"]: row for row in body["comparison"]["files"]["items"]}


def _session():
    generator = app.dependency_overrides[get_db]()
    return next(generator), generator


# ---------------------------------------------------------- empty state


def test_first_version_returns_an_informative_empty_state(client):
    first = _upload(client, V1)
    body = _compare(client, first["version"]["identity_id"])
    assert body["available"] is False and body["comparison"] is None
    assert "first version" in body["reason"]
    assert [v["version_number"] for v in body["versions"]] == [1]


# --------------------------------------------------- defaults and selection


def test_default_compares_latest_with_its_immediate_predecessor(client):
    _upload(client, V1)
    _upload(client, V2)
    third = _upload(client, V3)
    body = _compare(client, third["version"]["identity_id"])
    comparison = body["comparison"]
    assert (comparison["before"]["version_number"], comparison["after"]["version_number"]) == (2, 3)
    assert body["swapped"] is False
    assert [v["version_number"] for v in body["versions"]] == [1, 2, 3]


def test_any_two_versions_can_be_compared_and_order_is_normalized(client):
    _upload(client, V1)
    _upload(client, V2)
    third = _upload(client, V3)
    identity_id = third["version"]["identity_id"]

    v1_v3 = _compare(client, identity_id, **{"from": 1, "to": 3})["comparison"]
    assert (v1_v3["before"]["version_number"], v1_v3["after"]["version_number"]) == (1, 3)
    assert v1_v3["files"]["summary"]["added"] == 2  # added.py and another.py since V1

    reversed_body = _compare(client, identity_id, **{"from": 3, "to": 1})
    assert reversed_body["swapped"] is True
    assert reversed_body["comparison"]["before"]["version_number"] == 1

    only_to = _compare(client, identity_id, to=2)["comparison"]
    assert (only_to["before"]["version_number"], only_to["after"]["version_number"]) == (1, 2)
    only_from = _compare(client, identity_id, **{"from": 1})["comparison"]
    assert (only_from["before"]["version_number"], only_from["after"]["version_number"]) == (1, 3)


def test_invalid_selections(client):
    _upload(client, V1)
    second = _upload(client, V2)
    identity_id = second["version"]["identity_id"]
    assert _compare(client, 99999, expect=404)["detail"]
    assert _compare(client, identity_id, expect=404, **{"from": 1, "to": 9})["detail"]
    assert _compare(client, identity_id, expect=400, **{"from": 2, "to": 2})["detail"]
    assert _compare(client, identity_id, expect=400, to=1)["detail"]  # nothing earlier than V1
    assert _compare(client, identity_id, expect=400, **{"from": 2})["detail"]  # already the latest


# ------------------------------------------------- known file-level changes


def test_known_file_changes_are_detected_from_real_analysis(client):
    _upload(client, V1)
    second = _upload(client, V2)
    body = _compare(client, second["version"]["identity_id"])
    rows = _rows(body)

    assert rows["app/stable.py"]["status"] == "unchanged"
    assert rows["app/stable.py"]["verification"] == "content-hash"
    assert rows["app/hot.py"]["status"] == "changed"
    assert rows["app/removed.py"]["status"] == "removed"
    assert rows["app/added.py"]["status"] == "added"

    hot = rows["app/hot.py"]
    assert hot["verdict"] in {"regressed", "mixed"}
    complexity = hot["changes"]["complexity_total"]
    assert complexity["direction"] == "regressed"
    assert complexity["absolute_change"] >= 10 and complexity["percent_change"] > 100
    assert "complexity_total" in {s["rule"] for s in hot["signals"]}

    summary = body["comparison"]["files"]["summary"]
    assert (summary["added"], summary["removed"], summary["changed"], summary["unchanged"]) == (1, 1, 1, 1)
    assert "file:app/hot.py" in {a["id"] for a in body["comparison"]["regressions"]}


def test_project_cards_match_what_each_version_stored(client):
    first = _upload(client, V1)
    second = _upload(client, V2)
    body = _compare(client, second["version"]["identity_id"])
    metrics = body["comparison"]["project"]["metrics"]

    assert metrics["health_score"]["previous"] == first["health"]["score"]
    assert metrics["health_score"]["current"] == second["health"]["score"]
    assert metrics["average_complexity"]["previous"] == first["analysis"]["summary"]["average_complexity"]
    assert metrics["average_complexity"]["current"] == second["analysis"]["summary"]["average_complexity"]
    assert metrics["average_complexity"]["direction"] == "regressed"
    assert metrics["total_loc"]["previous"] == first["analysis"]["summary"]["total_loc"]
    assert metrics["files_analyzed"]["previous"] == 3 and metrics["files_analyzed"]["current"] == 3
    expected_delta = round(second["health"]["score"] - first["health"]["score"], 4)
    assert metrics["health_score"]["absolute_change"] == expected_delta
    risk = body["comparison"]["project"]["risk_distribution"]
    assert risk["available"] is True
    assert sum(l["current"] for l in risk["levels"].values()) == 3


def test_comparison_is_a_pure_read_and_deterministic(client):
    _upload(client, V1)
    second = _upload(client, V2)
    identity_id = second["version"]["identity_id"]
    assert _compare(client, identity_id) == _compare(client, identity_id)


# ------------------------------------------------------------ hashes


def test_fingerprints_are_persisted_with_each_scan(client):
    first = _upload(client, V1)
    session, generator = _session()
    try:
        rows = {f.file_path: f for f in session.query(FileFingerprint).filter_by(scan_id=first["project_id"]).all()}
        assert set(rows) == set(V1)
        assert rows["app/stable.py"].sha256 == hashlib.sha256(STABLE.encode()).hexdigest()
        assert rows["app/stable.py"].size_bytes == len(STABLE.encode())
    finally:
        generator.close()


def test_without_stored_hashes_unchanged_files_become_unverified(client):
    first = _upload(client, V1)
    second = _upload(client, V2)
    session, generator = _session()
    try:
        session.query(FileFingerprint).delete()  # simulate scans stored before Phase 9B
        session.commit()
    finally:
        generator.close()
    body = _compare(client, second["version"]["identity_id"])
    rows = _rows(body)
    assert rows["app/stable.py"]["status"] == "unverified"
    assert rows["app/hot.py"]["status"] == "changed"
    assert any("no stored file content hashes" in u for u in body["comparison"]["uncertainty"])
    assert first["project_id"] != second["project_id"]


# ------------------------------------------------------- ZIP root folders


def test_zip_root_directory_rename_does_not_mark_every_file_new(client):
    _upload(client, V1, "shop.zip", root="shop-v1")
    second = _upload(client, V2, "shop-v2.zip", root="shop-v2")
    assert second["version"]["version_number"] == 2  # identity survived the root rename
    body = _compare(client, second["version"]["identity_id"])
    summary = body["comparison"]["files"]["summary"]
    assert (summary["added"], summary["removed"], summary["changed"], summary["unchanged"]) == (1, 1, 1, 1)
    alignment = body["comparison"]["files"]["path_alignment"]
    assert alignment["previous_root_ignored"] == "shop-v1" and alignment["current_root_ignored"] == "shop-v2"
    assert "app/hot.py" in _rows(body)


# ------------------------------------------------- architecture / mixed


FASTAPI = (
    "from fastapi import FastAPI\napp = FastAPI()\n"
    "@app.get('/api/users')\ndef users():\n    return []\n"
)
FASTAPI_V2 = FASTAPI + "@app.delete('/api/users/{id}')\ndef remove(id):\n    return 1\n"
JS = "export function load() { return fetch('/api/users'); }\n"
JS_V2 = JS + "export function broken() { return fetch('/api/missing', { method: 'POST' }); }\n"


def test_architecture_changes_between_versions(client):
    _upload(client, {"backend/main.py": FASTAPI, "frontend/src/api.js": JS}, "shop.zip")
    second = _upload(client, {"backend/main.py": FASTAPI_V2, "frontend/src/api.js": JS_V2}, "shop.zip")
    arch = _compare(client, second["version"]["identity_id"])["comparison"]["architecture"]

    assert arch["available"] is True
    assert [e["key"] for e in arch["endpoints"]["added"]] == ["DELETE /api/users/{id}"]
    assert [e["key"] for e in arch["unmatched_calls"]["added"]] == ["POST /api/missing"]
    evidence = arch["unmatched_calls"]["added"][0]["evidence"][0]
    assert evidence["file"] == "frontend/src/api.js" and evidence["line"] == 2
    assert "arch:unmatched:POST /api/missing" in {a["id"] for a in arch["regressions"]}


def test_architecture_unavailable_is_disclosed_when_a_version_has_none(client):
    _upload(client, V1)
    second = _upload(client, V2)
    session, generator = _session()
    try:
        from app.database.models import ArchitectureScan

        session.query(ArchitectureScan).filter_by(project_id=second["project_id"]).delete()
        session.commit()
    finally:
        generator.close()
    comparison = _compare(client, second["version"]["identity_id"])["comparison"]
    assert comparison["architecture"]["available"] is False
    assert any("No saved architecture analysis" in u for u in comparison["uncertainty"])


def test_typescript_frontend_plus_java_backend_comparison(client):
    backend = {"backend/src/Api.java": "public class Api {\n    public int one() { return 1; }\n}\n"}
    frontend = {
        "frontend/src/api.ts": "export const load = () => fetch('/api/users');\n",
        "frontend/src/App.tsx": "export const App = () => null;\n",
    }
    _upload(client, {**backend, **frontend}, "mixed.zip")
    second = _upload(
        client,
        {**backend, **frontend, "backend/src/Extra.java": "public class Extra {\n    int two() { return 2; }\n}\n"},
        "mixed.zip",
    )
    comparison = _compare(client, second["version"]["identity_id"])["comparison"]
    languages = {l["language"]: l for l in comparison["project"]["languages"]}
    assert languages["java"]["files"]["previous"] == 1 and languages["java"]["files"]["current"] == 2
    assert languages["typescript"]["files"]["absolute_change"] == 0
    rows = _rows({"comparison": comparison})
    assert rows["backend/src/Extra.java"]["status"] == "added" and rows["backend/src/Extra.java"]["language"] == "java"
    assert rows["backend/src/Api.java"]["status"] == "unchanged"
    assert comparison["architecture"]["available"] is True


# --------------------------------------------------- earlier data intact


def test_comparing_never_modifies_stored_versions(client):
    _upload(client, V1)
    second = _upload(client, V2)
    session, generator = _session()
    try:
        before = [(v.id, v.version_number, v.health_score) for v in session.query(ProjectVersion).order_by(ProjectVersion.id)]
    finally:
        generator.close()
    _compare(client, second["version"]["identity_id"])
    session, generator = _session()
    try:
        after = [(v.id, v.version_number, v.health_score) for v in session.query(ProjectVersion).order_by(ProjectVersion.id)]
    finally:
        generator.close()
    assert before == after
    assert client.get(f"/api/projects/{second['project_id']}/history").status_code == 200
