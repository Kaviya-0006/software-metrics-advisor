"""
Tests for GET /api/projects/{project_id}/recommendations.

Uploads a real sample project through the existing pipeline (so the
project/module context is genuine, persisted data), then exercises the
recommendations endpoint with the AI provider mocked — never a real
network call.
"""

import io
import zipfile

import app.ai.advisor as advisor_module


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


def test_recommendations_404_for_missing_project(client):
    response = client.get("/api/projects/999999/recommendations")
    assert response.status_code == 404
    assert "Traceback" not in response.text


def test_recommendations_not_configured_by_default(client, monkeypatch):
    """With no AI_PROVIDER/AI_API_KEY set (the packaged default), the
    endpoint must return a clean unavailable response, not an error."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "none")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")

    uploaded = _upload(client, {"main.py": "def f():\n    return 1\n"})
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/recommendations")
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == project_id
    assert body["available"] is False
    assert body["recommendations"] == []
    assert body["message"] == "AI advisor is not configured."


def test_recommendations_successful_mocked_response(client, monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")

    mock_text = (
        '[{"problem": "High complexity", "severity": "HIGH", '
        '"explanation": "Nested branching increases path count.", '
        '"recommendation": "Extract helper functions.", "priority": 1}]'
    )
    monkeypatch.setattr(advisor_module, "_call_provider", lambda sp, uc: mock_text)

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

    response = client.get(f"/api/projects/{project_id}/recommendations")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert len(body["recommendations"]) == 1
    rec = body["recommendations"][0]
    assert rec["problem"] == "High complexity"
    assert rec["severity"] == "HIGH"
    assert rec["priority"] == 1


def test_recommendations_provider_failure_returns_clean_response(client, monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")

    def _raise(sp, uc):
        raise advisor_module.AIProviderError("Could not reach the AI provider.")

    monkeypatch.setattr(advisor_module, "_call_provider", _raise)

    uploaded = _upload(client, {"main.py": "def f():\n    return 1\n"})
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/recommendations")
    assert response.status_code == 200  # a degraded AI is not an HTTP error
    body = response.json()
    assert body["available"] is False
    assert body["recommendations"] == []
    assert "fake-key" not in response.text
    assert "Traceback" not in response.text


def test_recommendations_does_not_change_persisted_health_or_risk(client, monkeypatch):
    """Calling the recommendations endpoint must never alter the
    project's already-persisted health score, category scores, or any
    module's risk score — it only reads them."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")
    monkeypatch.setattr(advisor_module, "_call_provider", lambda sp, uc: "[]")

    uploaded = _upload(client, {
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    })
    project_id = uploaded["project_id"]

    before = client.get(f"/api/projects/{project_id}").json()
    client.get(f"/api/projects/{project_id}/recommendations")
    after = client.get(f"/api/projects/{project_id}").json()

    assert before["health"] == after["health"]
    assert before["category_scores"] == after["category_scores"]
    assert before["modules"] == after["modules"]
    assert before["risky_modules"] == after["risky_modules"]


def test_recommendations_context_includes_real_persisted_values(client, monkeypatch):
    """Verify the context actually sent to the (mocked) provider matches
    what the database has persisted — not recalculated on the fly."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")

    captured = {}

    def _capture(system_prompt, user_content):
        captured["system_prompt"] = system_prompt
        captured["user_content"] = user_content
        return "[]"

    monkeypatch.setattr(advisor_module, "_call_provider", _capture)

    uploaded = _upload(client, {
        "main.py": "class Foo:\n    def bar(self):\n        return 1\n",
    })
    project_id = uploaded["project_id"]
    expected_health_score = uploaded["health"]["score"]

    client.get(f"/api/projects/{project_id}/recommendations")

    import json
    sent_context = json.loads(captured["user_content"])
    assert sent_context["project"]["health_score"] == expected_health_score
    assert "authoritative" in captured["system_prompt"].lower()


def test_recommendations_endpoint_with_ollama_provider(client, monkeypatch):
    """End-to-end through the real HTTP endpoint with AI_PROVIDER=ollama —
    confirms provider selection, model, and URL all flow correctly from
    config through to the (mocked) HTTP call, and that no API key is
    required for this provider."""
    import httpx

    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")  # no key needed
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(advisor_module.settings, "AI_MODEL", "qwen2.5:3b")

    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["model"] = json["model"]
        captured["timeout"] = timeout
        response_text = (
            '[{"problem": "High complexity", "severity": "HIGH", '
            '"explanation": "Nested branching.", "recommendation": "Extract methods.", '
            '"priority": 1}]'
        )
        return httpx.Response(
            200,
            json={"message": {"content": response_text}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)

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

    response = client.get(f"/api/projects/{project_id}/recommendations")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["recommendations"][0]["severity"] == "HIGH"

    # Confirm the real config values were actually used for the call.
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["model"] == "qwen2.5:3b"


def test_recommendations_endpoint_ollama_unreachable(client, monkeypatch):
    """Simulates the exact situation confirmed in this sandbox: Ollama is
    not running. The endpoint must still respond cleanly, not crash."""
    import httpx

    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    def fake_post(url, json, timeout):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)

    uploaded = _upload(client, {"main.py": "def f():\n    return 1\n"})
    project_id = uploaded["project_id"]

    response = client.get(f"/api/projects/{project_id}/recommendations")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["recommendations"] == []
    assert "Traceback" not in response.text
    assert "11434" not in response.text
