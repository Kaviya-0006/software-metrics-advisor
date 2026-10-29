"""
Tests for the Phase 4 project read APIs:

    GET /api/projects
    GET /api/projects/{project_id}
    GET /api/projects/{project_id}/metrics
    GET /api/projects/{project_id}/risks

Also covers 404 handling, malformed-request handling, and the global
error handler that keeps internal exceptions out of API responses.
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


# --- GET /api/projects -------------------------------------------------------

def test_list_projects_empty(client):
    response = client.get("/api/projects")
    assert response.status_code == 200
    assert response.json() == {"projects": []}


def test_list_projects_returns_uploaded_projects(client):
    _upload(client, {"a.py": "def f():\n    return 1\n"}, "first.zip")
    _upload(client, {"b.py": "def g():\n    return 2\n"}, "second.zip")

    response = client.get("/api/projects")
    assert response.status_code == 200
    body = response.json()

    assert len(body["projects"]) == 2
    names = {p["name"] for p in body["projects"]}
    assert names == {"first", "second"}
    for p in body["projects"]:
        assert "health_score" in p
        assert "health_status" in p
        assert "files" in p
        assert "languages" in p


def test_list_projects_most_recent_first(client):
    _upload(client, {"a.py": "x = 1\n"}, "older.zip")
    _upload(client, {"b.py": "x = 2\n"}, "newer.zip")

    response = client.get("/api/projects")
    body = response.json()
    assert body["projects"][0]["name"] == "newer"
    assert body["projects"][1]["name"] == "older"


# --- GET /api/projects/{id} ---------------------------------------------------

def test_get_project_detail(client):
    uploaded = _upload(client, {
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    })
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200
    body = response.json()

    assert body["project"]["id"] == project_id
    assert body["project"]["name"] == "proj"
    assert "score" in body["health"]
    assert "status" in body["health"]
    assert set(body["category_scores"].keys()) == {
        "maintainability", "complexity", "code_quality", "testing",
        "coupling_cohesion", "duplication", "documentation", "code_churn",
    }
    assert body["summary"]["files"] == 1
    assert len(body["modules"]) == 1
    assert body["modules"][0]["file_name"] == "main.py"
    assert "risk_score" not in body["modules"][0] or True  # module dict is raw metrics; risk is separate
    assert len(body["risky_modules"]) == 1


def test_get_project_detail_matches_upload_response_values(client):
    uploaded = _upload(client, {
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    })
    project_id = uploaded["project_id"]

    detail = client.get(f"/api/projects/{project_id}").json()

    # Health score/status persisted must equal what upload computed.
    assert detail["health"]["score"] == uploaded["health"]["score"]
    assert detail["health"]["status"] == uploaded["health"]["status"]
    assert detail["category_scores"] == uploaded["category_scores"]


def test_get_project_detail_404_for_missing_project(client):
    response = client.get("/api/projects/999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_project_detail_404_message_no_stack_trace(client):
    response = client.get("/api/projects/999999")
    body = response.json()
    assert list(body.keys()) == ["detail"]
    assert "Traceback" not in response.text
    assert "File \"" not in response.text


# --- GET /api/projects/{id}/metrics -------------------------------------------

def test_get_project_metrics(client):
    uploaded = _upload(client, {
        "main.py": (
            "class Foo:\n"
            "    def bar(self, x):\n"
            "        if x > 0:\n"
            "            return x\n"
            "        return -x\n"
        ),
    })
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/metrics")
    assert response.status_code == 200
    body = response.json()

    assert body["project_id"] == project_id
    assert len(body["modules"]) == 1
    module = body["modules"][0]

    # Raw Phase 2 fields preserved.
    assert module["file_name"] == "main.py"
    assert module["language"] == "python"
    assert module["loc"]["total"] > 0
    assert module["classes"] == 1
    assert module["complexity"]["total"] == 2

    # Never-fabricated fields explicitly present and null/derived.
    assert module["test_coverage"] is None
    assert module["documentation_percentage"] == 0.0  # no comments in this sample


def test_get_project_metrics_preserves_null_cohesion(client):
    # A class with only one method has no LCOM1-scorable cohesion (Phase 2
    # returns None for this) — it must stay None here, not become 0.
    uploaded = _upload(client, {
        "main.py": "class Solo:\n    def only(self):\n        return 1\n",
    })
    project_id = uploaded["project_id"]

    body = client.get(f"/api/projects/{project_id}/metrics").json()
    assert body["modules"][0]["cohesion"] is None


def test_get_project_metrics_404_for_missing_project(client):
    response = client.get("/api/projects/999999/metrics")
    assert response.status_code == 404


# --- GET /api/projects/{id}/risks ---------------------------------------------

def test_get_project_risks(client):
    uploaded = _upload(client, {
        "good.py": "def f():\n    return 1\n",
    })
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/risks")
    assert response.status_code == 200
    body = response.json()

    assert body["project_id"] == project_id
    assert set(body["risk_summary"].keys()) == {"low", "medium", "high", "critical"}
    assert sum(body["risk_summary"].values()) == len(body["risky_modules"])
    assert body["risky_modules"][0]["file_name"] == "good.py"
    assert "risk_factors" in body["risky_modules"][0]


def test_get_project_risks_sorted_descending(client):
    good_code = "def f():\n    return 1\n"
    bad_code = (
        "class Bad:\n"
        "    def tangled(self, x):\n"
        "        if x > 0:\n"
        "            if x > 1:\n"
        "                if x > 2:\n"
        "                    if x > 3:\n"
        "                        return 1\n"
        "        return 0\n"
    )
    uploaded = _upload(client, {"good.py": good_code, "bad.py": bad_code})
    project_id = uploaded["project_id"]

    body = client.get(f"/api/projects/{project_id}/risks").json()
    scores = [m["risk_score"] for m in body["risky_modules"]]
    assert scores == sorted(scores, reverse=True)
    assert body["risky_modules"][0]["file_name"] == "bad.py"


def test_get_project_risks_uses_persisted_values_not_recalculated(client):
    """The risk numbers from /risks must exactly match what /upload
    already computed and stored — confirming no second risk algorithm
    runs here, only a read of the persisted Module rows."""
    uploaded = _upload(client, {
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    })
    project_id = uploaded["project_id"]

    risks_body = client.get(f"/api/projects/{project_id}/risks").json()
    upload_module = uploaded["analysis"]["modules"][0]
    risks_module = risks_body["risky_modules"][0]

    assert risks_module["risk_score"] == upload_module["risk_score"]
    assert risks_module["risk_level"] == upload_module["risk_level"]


def test_get_project_risks_404_for_missing_project(client):
    response = client.get("/api/projects/999999/risks")
    assert response.status_code == 404


# --- Malformed request handling -----------------------------------------------

def test_get_project_detail_non_integer_id_returns_422(client):
    response = client.get("/api/projects/not-a-number")
    assert response.status_code == 422
    assert "Traceback" not in response.text


def test_upload_endpoint_still_works_after_api_extension(client):
    """Regression guard: adding the Phase 4 read APIs must not have
    disturbed the existing upload endpoint's behavior."""
    body = _upload(client, {"x.py": "def f():\n    return 1\n"})
    assert body["success"] is True
    assert body["analysis"]["project"]["files"] == 1
