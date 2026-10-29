"""
Tests for GET /api/projects/{project_id}/report.

Uploads a real project through the existing pipeline (so the PDF is built
from genuine persisted data, not hand-constructed), then verifies the
HTTP contract (status, content type, filename, PDF signature) and that
the rendered PDF text preserves the 0-vs-null distinction exactly like
every other endpoint in this project.
"""

import io
import zipfile

from pypdf import PdfReader


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


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "".join(page.extract_text() for page in reader.pages)


def test_report_valid_project_returns_200_pdf(client):
    uploaded = _upload(client, {"main.py": "def f():\n    return 1\n"})
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/report")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:4] == b"%PDF"


def test_report_has_sensible_filename(client):
    uploaded = _upload(client, {"main.py": "def f():\n    return 1\n"})
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/report")
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert f"software-health-report-{project_id}.pdf" in disposition


def test_report_404_for_missing_project(client):
    response = client.get("/api/projects/999999/report")
    assert response.status_code == 404
    assert "Traceback" not in response.text


def test_report_malformed_id_returns_422(client):
    response = client.get("/api/projects/not-a-number/report")
    assert response.status_code == 422


def test_report_contains_project_name_and_health_score(client):
    uploaded = _upload(client, {
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    }, "myproject.zip")
    project_id = uploaded["project_id"]
    expected_score = uploaded["health"]["score"]

    response = client.get(f"/api/projects/{project_id}/report")
    text = _pdf_text(response.content)

    assert "myproject" in text
    assert f"{expected_score}" in text


def test_report_genuine_zero_documentation_is_not_na(client):
    """A file with real code and zero comments has documentation_percentage
    = 0.0, a genuine measured value -- must render as 0.0 in the PDF, not
    N/A."""
    uploaded = _upload(client, {
        "NoComments.py": "def f():\n    return 1\n",  # code, zero comments
    })
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/report")
    text = _pdf_text(response.content)

    assert "0.0" in text


def test_report_null_testing_and_code_churn_render_as_na(client):
    uploaded = _upload(client, {"main.py": "def f():\n    return 1\n"})
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/report")
    text = _pdf_text(response.content)

    assert "Testing" in text
    assert "Code Churn" in text
    assert "N/A" in text


def test_report_single_scan_shows_unavailable_trend_message(client):
    uploaded = _upload(client, {"main.py": "def f():\n    return 1\n"})
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/report")
    text = _pdf_text(response.content)

    assert "Historical trend unavailable" in text


def test_report_multi_scan_shows_history_table(client):
    a = _upload(client, {"main.py": "def f():\n    return 1\n"}, "same.zip")
    b = _upload(client, {
        "main.py": "def f():\n    return 1\n",
        "extra.py": "def g():\n    return 2\n",
    }, "same.zip")

    response = client.get(f"/api/projects/{b['project_id']}/report")
    text = _pdf_text(response.content)

    assert "Historical trend unavailable" not in text
    assert "Scans in History" in text


def test_report_includes_same_evidence_backed_recommendations_as_advisor(client):
    """The PDF must not silently disagree with the dashboard advisory endpoint.
    When deterministic fallback finds a measurable issue, the PDF includes the
    recommendation, file, risk and measured evidence."""
    uploaded = _upload(client, {
        "PaymentService.java": (
            "public class PaymentService {\n"
            "  public int pay(int amount) {\n"
            "    int x=0; if(amount>0)x++; if(amount>1)x++; if(amount>2)x++; "
            "if(amount>3)x++; if(amount>4)x++; if(amount>5)x++; if(amount>6)x++; "
            "if(amount>7)x++; if(amount>8)x++; if(amount>9)x++; if(amount>10)x++; "
            "return x;\n  }\n}\n"
        )
    }, "recommendation.zip")
    project_id = uploaded["project_id"]

    advisory = client.get(f"/api/projects/{project_id}/recommendations")
    assert advisory.status_code == 200
    recommendations = advisory.json()["recommendations"]
    assert recommendations

    response = client.get(f"/api/projects/{project_id}/report")
    text = _pdf_text(response.content)

    assert "AI Recommendations" in text
    assert recommendations[0]["file_path"] in text
    assert "Measured evidence" in text
    assert "Recommendation" in text


def test_report_shows_fallback_recommendations_when_ai_unavailable(client):
    uploaded = _upload(client, {
        "PaymentService.java": (
            "public class PaymentService {\n"
            "  public int pay(int amount) {\n"
            "    if(amount>0){} if(amount>1){} if(amount>2){} if(amount>3){} "
            "if(amount>4){} if(amount>5){} if(amount>6){} if(amount>7){} "
            "if(amount>8){} if(amount>9){} if(amount>10){} return amount;\n"
            "  }\n}\n"
        )
    }, "fallback.zip")
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/report")
    text = _pdf_text(response.content)

    assert "AI Recommendations" in text
    assert "PaymentService.java" in text
    assert "Measured evidence" in text


def test_report_does_not_mutate_persisted_data(client):
    uploaded = _upload(client, {
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    })
    project_id = uploaded["project_id"]

    before = client.get(f"/api/projects/{project_id}").json()
    client.get(f"/api/projects/{project_id}/report")
    after = client.get(f"/api/projects/{project_id}").json()

    assert before == after


def test_report_uses_real_risk_data(client):
    uploaded = _upload(client, {
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    })
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/report")
    text = _pdf_text(response.content)

    assert "Risk Summary" in text
    assert "main.py" in text
