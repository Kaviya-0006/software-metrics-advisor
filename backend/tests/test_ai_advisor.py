"""
Unit tests for app.ai.advisor — context construction and response
normalization. No network calls: _call_provider is monkeypatched in every
test that needs a "successful" or "failing" provider response.
"""

import pytest

from app.ai.advisor import (
    AdvisorResult,
    AIProviderError,
    Recommendation,
    build_ai_context,
    generate_recommendations,
    is_ai_configured,
)
import app.ai.advisor as advisor_module


# --- is_ai_configured -------------------------------------------------------

def test_not_configured_when_provider_is_none(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "none")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "some-key")
    assert is_ai_configured() is False


def test_not_configured_when_key_missing(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    assert is_ai_configured() is False


def test_configured_when_provider_and_key_present(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "some-key")
    assert is_ai_configured() is True


# --- build_ai_context: no recalculation, null/zero preserved ----------------

def test_context_includes_health_and_category_scores():
    project_info = {
        "name": "demo",
        "health_score": 79.58,
        "health_status": "HEALTHY",
        "category_scores": {"maintainability": 72.0, "testing": None},
        "summary": {"files": 3, "classes": 5, "functions": 10, "total_loc": 200},
    }
    context = build_ai_context(project_info, [])
    assert context["project"]["health_score"] == 79.58
    assert context["project"]["health_status"] == "HEALTHY"
    assert context["project"]["category_scores"]["maintainability"] == 72.0
    assert context["project"]["category_scores"]["testing"] is None
    assert context["project"]["summary"]["files"] == 3


def test_context_includes_module_metrics_and_risk():
    modules_info = [{
        "file_name": "Foo.py",
        "language": "python",
        "loc": {"total": 100, "code": 80, "comments": 10, "blank": 10},
        "classes": 1,
        "functions": 5,
        "complexity": {"total": 10, "average": 2.0, "maximum": 4},
        "maintainability_index": 70.0,
        "coupling": 3,
        "cohesion": 0.0,
        "depth_of_inheritance": 1,
        "duplication_percentage": 5.0,
        "test_coverage": None,
        "documentation_percentage": 12.5,
        "risk_score": 22.4,
        "risk_level": "LOW",
        "risk_factors": [],
    }]
    context = build_ai_context({}, modules_info)
    module = context["modules"][0]
    assert module["file_name"] == "Foo.py"
    assert module["risk_score"] == 22.4
    assert module["risk_level"] == "LOW"
    assert module["risk_factors"] == []


def test_context_preserves_null_as_null_not_missing():
    modules_info = [{
        "file_name": "Foo.py", "test_coverage": None, "cohesion": None,
    }]
    context = build_ai_context({}, modules_info)
    assert context["modules"][0]["test_coverage"] is None
    assert context["modules"][0]["cohesion"] is None


def test_context_preserves_genuine_zero_not_as_missing():
    """A real zero (e.g. zero coupling, zero duplication) must survive as
    0, never silently become null via a truthiness check."""
    modules_info = [{
        "file_name": "Base.py", "coupling": 0, "duplication_percentage": 0.0,
        "depth_of_inheritance": 0,
    }]
    context = build_ai_context({}, modules_info)
    module = context["modules"][0]
    assert module["coupling"] == 0
    assert module["coupling"] is not None
    assert module["duplication_percentage"] == 0.0
    assert module["depth_of_inheritance"] == 0


# --- generate_recommendations: not configured --------------------------------

def test_generate_recommendations_not_configured(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "none")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is False
    assert result.recommendations == []
    assert result.message == "AI advisor is not configured."


# --- generate_recommendations: successful mocked response -------------------

def test_generate_recommendations_successful_single(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")

    mock_response = (
        '[{"problem": "High complexity in bar()", "severity": "HIGH", '
        '"explanation": "Cyclomatic complexity of 24 means many independent paths.", '
        '"recommendation": "Split bar() into smaller functions.", "priority": 1}]'
    )
    monkeypatch.setattr(advisor_module, "_call_provider", lambda sp, uc: mock_response)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is True
    assert result.message is None
    assert len(result.recommendations) == 1
    rec = result.recommendations[0]
    assert rec.problem == "High complexity in bar()"
    assert rec.severity == "HIGH"
    assert rec.priority == 1


def test_generate_recommendations_multiple(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")

    mock_response = """
    [
      {"problem": "A", "severity": "CRITICAL", "explanation": "e1", "recommendation": "r1", "priority": 1},
      {"problem": "B", "severity": "LOW", "explanation": "e2", "recommendation": "r2", "priority": 2}
    ]
    """
    monkeypatch.setattr(advisor_module, "_call_provider", lambda sp, uc: mock_response)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is True
    assert len(result.recommendations) == 2
    assert result.recommendations[0].severity == "CRITICAL"
    assert result.recommendations[1].severity == "LOW"


def test_generate_recommendations_empty_list_is_available(monkeypatch):
    """A clean-project response (no issues found) is still `available: true`
    with an empty list — not treated as an error."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")
    monkeypatch.setattr(advisor_module, "_call_provider", lambda sp, uc: "[]")

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is True
    assert result.recommendations == []


# --- generate_recommendations: malformed output ------------------------------

def test_generate_recommendations_malformed_json(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")
    monkeypatch.setattr(advisor_module, "_call_provider", lambda sp, uc: "not json at all {{{")

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is False
    assert result.recommendations == []
    assert "invalid" in result.message.lower()


def test_generate_recommendations_wrong_root_type(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")
    # A JSON object instead of the required array.
    monkeypatch.setattr(advisor_module, "_call_provider", lambda sp, uc: '{"not": "a list"}')

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is False


def test_generate_recommendations_partial_malformed_entries_are_filtered(monkeypatch):
    """A list containing one valid and one invalid entry keeps the valid
    one rather than discarding the whole response."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")
    mock_response = """
    [
      {"problem": "Valid", "severity": "MEDIUM", "explanation": "e", "recommendation": "r", "priority": 1},
      {"problem": "Missing fields"},
      "not even a dict"
    ]
    """
    monkeypatch.setattr(advisor_module, "_call_provider", lambda sp, uc: mock_response)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is True
    assert len(result.recommendations) == 1
    assert result.recommendations[0].problem == "Valid"


def test_invalid_severity_is_coerced_to_medium(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")
    mock_response = (
        '[{"problem": "P", "severity": "SUPER_BAD", "explanation": "e", '
        '"recommendation": "r", "priority": 1}]'
    )
    monkeypatch.setattr(advisor_module, "_call_provider", lambda sp, uc: mock_response)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.recommendations[0].severity == "MEDIUM"


# --- generate_recommendations: provider failure ------------------------------

def test_generate_recommendations_provider_failure(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "fake-key")

    def _raise(sp, uc):
        raise AIProviderError("Could not reach the AI provider.")

    monkeypatch.setattr(advisor_module, "_call_provider", _raise)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is False
    assert result.recommendations == []
    assert "temporarily unavailable" in result.message.lower()


def test_provider_failure_message_never_contains_api_key(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "super-secret-key-value")

    def _raise(sp, uc):
        raise AIProviderError("Could not reach the AI provider.")

    monkeypatch.setattr(advisor_module, "_call_provider", _raise)

    result = generate_recommendations({"project": {}, "modules": []})
    assert "super-secret-key-value" not in (result.message or "")


def test_unsupported_provider_raises_ai_provider_error(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "some-unsupported-provider")
    with pytest.raises(AIProviderError):
        advisor_module._call_provider("system", "user")
