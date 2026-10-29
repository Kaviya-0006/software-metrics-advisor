"""
Tests for GET /api/projects/{project_id}/history.

Uploads the same ZIP filename multiple times to build genuine multi-scan
history through the real pipeline (not hand-constructed rows), then
verifies chronological ordering, exact value preservation (including 0
vs null), 404 handling, and that calling history never mutates any
persisted data.
"""

import io
import zipfile


def _make_zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _upload(client, files: dict[str, str], zip_name: str = "proj.zip") -> dict:
    zip_bytes = _make_zip_bytes(files)
    response = client.post(
        "/api/projects/upload",
        files={"file": (zip_name, zip_bytes, "application/zip")},
    )
    assert response.status_code == 200
    return response.json()


def test_history_200_for_valid_project(client):
    uploaded = _upload(client, {"main.py": "def f():\n    return 1\n"})
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/history")
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == project_id
    assert body["project_name"] == "proj"
    assert len(body["history"]) == 1


def test_history_multiple_scans_preserved_and_ordered(client):
    # Same filename ("same logical project") uploaded three times with
    # different content, producing three distinct Project rows/scans.
    first = _upload(client, {"main.py": "def f():\n    return 1\n"}, "same.zip")
    second = _upload(client, {
        "main.py": "def f():\n    return 1\n",
        "extra.py": "def g():\n    return 2\n",
    }, "same.zip")
    third = _upload(client, {
        "main.py": "def f():\n    return 1\n",
        "extra.py": "def g():\n    return 2\n",
        "more.py": "def h():\n    return 3\n",
    }, "same.zip")

    response = client.get(f"/api/projects/{third['project_id']}/history")
    assert response.status_code == 200
    body = response.json()

    assert len(body["history"]) == 3
    scan_ids = [h["scan_id"] for h in body["history"]]
    assert scan_ids == [first["project_id"], second["project_id"], third["project_id"]]

    # Chronological: scanned_at strictly non-decreasing.
    timestamps = [h["scanned_at"] for h in body["history"]]
    assert timestamps == sorted(timestamps)


def test_history_health_and_category_scores_match_persisted_values(client):
    uploaded = _upload(client, {
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    })
    project_id = uploaded["project_id"]

    history_body = client.get(f"/api/projects/{project_id}/history").json()
    detail_body = client.get(f"/api/projects/{project_id}").json()

    entry = history_body["history"][0]
    assert entry["health_score"] == detail_body["health"]["score"]
    assert entry["health_status"] == detail_body["health"]["status"]
    assert entry["category_scores"] == detail_body["category_scores"]


def test_history_preserves_zero_category_score_not_as_null(client):
    """documentation category score can legitimately be a real 0.0 (zero
    comment density) -- must never be reported as null/missing."""
    uploaded = _upload(client, {
        "NoComments.py": "def f():\n    return 1\n",  # code, zero comments
    })
    project_id = uploaded["project_id"]

    history_body = client.get(f"/api/projects/{project_id}/history").json()
    doc_score = history_body["history"][0]["category_scores"]["documentation"]
    assert doc_score == 0.0
    assert doc_score is not None


def test_history_preserves_null_category_scores(client):
    """testing/code_churn categories are always null (no data source) —
    must remain null in history, never coerced to 0."""
    uploaded = _upload(client, {"main.py": "def f():\n    return 1\n"})
    project_id = uploaded["project_id"]

    history_body = client.get(f"/api/projects/{project_id}/history").json()
    categories = history_body["history"][0]["category_scores"]
    assert categories["testing"] is None
    assert categories["code_churn"] is None


def test_history_404_for_missing_project(client):
    response = client.get("/api/projects/999999/history")
    assert response.status_code == 404
    assert "Traceback" not in response.text


def test_history_malformed_project_id_returns_422(client):
    response = client.get("/api/projects/not-a-number/history")
    assert response.status_code == 422


def test_history_does_not_mutate_persisted_data(client):
    """Calling /history must be a pure read -- confirm the project's
    detail/risks/metrics are byte-identical before and after."""
    uploaded = _upload(client, {
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    })
    project_id = uploaded["project_id"]

    before_detail = client.get(f"/api/projects/{project_id}").json()
    before_risks = client.get(f"/api/projects/{project_id}/risks").json()

    client.get(f"/api/projects/{project_id}/history")

    after_detail = client.get(f"/api/projects/{project_id}").json()
    after_risks = client.get(f"/api/projects/{project_id}/risks").json()

    assert before_detail == after_detail
    assert before_risks == after_risks


def test_history_does_not_include_module_level_data(client):
    uploaded = _upload(client, {
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    })
    project_id = uploaded["project_id"]

    body = client.get(f"/api/projects/{project_id}/history").json()
    entry = body["history"][0]
    assert "modules" not in entry
    assert "risky_modules" not in entry


def test_history_does_not_group_unrelated_project_names(client):
    """Two uploads with different filenames must NOT be merged into one
    history — grouping is by exact project name only."""
    a = _upload(client, {"main.py": "def f():\n    return 1\n"}, "alpha.zip")
    b = _upload(client, {"main.py": "def g():\n    return 2\n"}, "beta.zip")

    history_a = client.get(f"/api/projects/{a['project_id']}/history").json()
    history_b = client.get(f"/api/projects/{b['project_id']}/history").json()

    assert len(history_a["history"]) == 1
    assert len(history_b["history"]) == 1
    assert history_a["history"][0]["scan_id"] != history_b["history"][0]["scan_id"]
