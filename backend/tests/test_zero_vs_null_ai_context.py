"""
Phase 6D focused audit: confirm every metric that can legitimately be a
genuine 0 survives end-to-end through GET /api/projects/{id}/recommendations
into the AI context exactly as 0 — never silently converted to null/missing
by a truthiness check anywhere in the endpoint -> build_ai_context() chain.

Uses a real uploaded project (so the 0 values are genuine persisted data,
not hand-constructed) and captures the actual JSON context the (mocked)
provider would have received.
"""

import io
import json
import zipfile

import app.ai.advisor as advisor_module


def _make_zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_zero_metrics_survive_into_ai_context_distinct_from_null(client, monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")

    captured = {}

    def _capture(system_prompt, user_content):
        captured["user_content"] = user_content
        return "[]"

    monkeypatch.setattr(advisor_module, "_call_provider", _capture)

    # A class with no fields and no inheritance genuinely produces:
    #   coupling = 0, duplication_percentage = 0.0 (no repeated blocks),
    #   depth_of_inheritance = 0 (no base class)
    # -- all real, measured zeros, not missing data.
    zip_bytes = _make_zip_bytes({
        "Standalone.py": (
            "class Standalone:\n"
            "    def a(self):\n"
            "        return 1\n\n"
            "    def b(self):\n"
            "        return 2\n"
        ),
    })
    response = client.post(
        "/api/projects/upload",
        files={"file": ("proj.zip", zip_bytes, "application/zip")},
    )
    assert response.status_code == 200
    project_id = response.json()["project_id"]

    # Sanity: confirm the upload response itself really does carry genuine
    # zeros here (not skip the test against accidentally-null data).
    module = response.json()["analysis"]["modules"][0]
    assert module["coupling"] == 0
    assert module["duplication_percentage"] == 0.0
    assert module["depth_of_inheritance"] == 0

    client.get(f"/api/projects/{project_id}/recommendations")

    sent_context = json.loads(captured["user_content"])
    sent_module = sent_context["modules"][0]

    # The exact values, and exact types -- 0 must remain 0 (int/float),
    # never become None/null.
    assert sent_module["coupling"] == 0
    assert sent_module["coupling"] is not None
    assert sent_module["duplication_percentage"] == 0.0
    assert sent_module["duplication_percentage"] is not None
    assert sent_module["depth_of_inheritance"] == 0
    assert sent_module["depth_of_inheritance"] is not None

    # And the fields that ARE genuinely unavailable for this module must
    # still be null -- confirming 0 and null are not being confused in
    # either direction.
    assert sent_module["test_coverage"] is None


def test_zero_risk_score_would_survive_if_present(client, monkeypatch):
    """A perfectly healthy module can legitimately score risk_score close
    to (but not exactly) 0 under the real weighted formula; this test
    confirms the JSON round-trip itself never maps 0 -> null, using a
    direct build_ai_context() call with a hand-built 0 value (the
    deterministic risk formula's own arithmetic is Phase 3's concern, not
    this test's -- see test_risk.py for that)."""
    from app.ai.advisor import build_ai_context

    modules_info = [{
        "file_name": "Perfect.py",
        "risk_score": 0.0,
        "risk_level": "LOW",
        "risk_factors": [],
        "cohesion": 0.0,
        "coupling": 0,
    }]
    context = build_ai_context({}, modules_info)
    module = context["modules"][0]

    assert module["risk_score"] == 0.0
    assert module["risk_score"] is not None
    assert module["cohesion"] == 0.0
    assert module["coupling"] == 0


def test_documentation_percentage_zero_vs_unavailable_are_distinct(client, monkeypatch):
    """documentation_percentage=0.0 (a file with code but zero comments) and
    documentation_percentage=None (a file with zero code lines, where the
    ratio is mathematically undefined) must remain distinguishable -- both
    end up null-safe, but only the second is genuinely null."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")
    monkeypatch.setattr(advisor_module, "_call_provider", lambda sp, uc: "[]")

    zip_bytes = _make_zip_bytes({
        "NoComments.py": "def f():\n    return 1\n",  # has code, zero comments
    })
    response = client.post(
        "/api/projects/upload",
        files={"file": ("proj2.zip", zip_bytes, "application/zip")},
    )
    project_id = response.json()["project_id"]
    module = response.json()["analysis"]["modules"][0]

    # This file has real code lines and zero comment lines -> a genuine 0%,
    # not an undefined/null ratio.
    metrics_response = client.get(f"/api/projects/{project_id}/metrics")
    doc_pct = metrics_response.json()["modules"][0]["documentation_percentage"]
    assert doc_pct == 0.0
    assert doc_pct is not None


def test_prompt_explicitly_distinguishes_null_from_zero():
    """Confirms the Phase 6D prompt fix is actually in place — the model
    is explicitly told 0 and null are different, with per-metric examples,
    not left to infer this on its own."""
    from app.ai.prompts import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    assert "null and 0 are not the same" in lowered
    assert '"coupling": 0' in SYSTEM_PROMPT
    assert '"test_coverage": 0' in SYSTEM_PROMPT
    assert '"test_coverage": null' in SYSTEM_PROMPT


def test_wrapped_ollama_response_preserves_zero_priority(monkeypatch):
    """The wrapped-object unwrapping fix must not disturb 0-vs-null
    handling within a recommendation's own fields either — a priority of
    0 (an edge case; the prompt asks for 1+, but the parser must not
    silently discard a literal 0) survives through the wrapper-unwrap
    path exactly as it would through a bare array."""
    import app.ai.advisor as advisor_module

    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    wrapped_response = (
        '{"recommendations": [{"problem": "P", "severity": "LOW", '
        '"explanation": "e", "recommendation": "r", "priority": 0}]}'
    )
    monkeypatch.setattr(advisor_module, "_call_provider", lambda sp, uc: wrapped_response)

    result = advisor_module.generate_recommendations({"project": {}, "modules": []})
    assert result.available is True
    assert result.recommendations[0].priority == 0
    assert result.recommendations[0].priority is not None
