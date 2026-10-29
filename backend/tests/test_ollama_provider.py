"""
Tests for the Ollama provider path added in Phase 6B.

Mocks httpx.post directly (returning real httpx.Response objects built
in-memory) rather than hitting a real Ollama server — the automated suite
must never depend on Ollama being installed or running. Live verification
against a real local Ollama instance is a separate, manual step (see the
Phase 6B report) and is explicitly skipped here because Ollama is not
available in this environment.
"""

import httpx
import pytest

import app.ai.advisor as advisor_module
from app.ai.advisor import (
    AIProviderError,
    _call_ollama,
    _call_provider,
    generate_recommendations,
    is_ai_configured,
)


def _fake_ollama_response(status_code: int, json_body: dict) -> httpx.Response:
    """Build a real httpx.Response with no network I/O, so raise_for_status()
    and .json() behave exactly like a genuine response would."""
    return httpx.Response(
        status_code,
        json=json_body,
        request=httpx.Request("POST", "http://localhost:11434/api/chat"),
    )


# --- Configuration: Ollama needs no API key ---------------------------------

def test_ollama_configured_without_api_key(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")  # deliberately empty
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    assert is_ai_configured() is True


def test_ollama_not_configured_without_base_url(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "")
    assert is_ai_configured() is False


def test_anthropic_still_requires_api_key_after_ollama_support_added(monkeypatch):
    """Regression guard: adding the Ollama path must not have loosened the
    Anthropic path's requirement for a real API key."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    assert is_ai_configured() is False

    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "real-key")
    assert is_ai_configured() is True


def test_provider_none_never_configured_regardless_of_ollama_url(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "none")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    assert is_ai_configured() is False


# --- _call_ollama: request/response handling --------------------------------

def test_call_ollama_successful_response(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(advisor_module.settings, "AI_MODEL", "qwen2.5:3b")
    monkeypatch.setattr(advisor_module.settings, "AI_REQUEST_TIMEOUT_SECONDS", 30)

    expected_text = '[{"problem": "P", "severity": "LOW", "explanation": "e", "recommendation": "r", "priority": 1}]'

    def fake_post(url, json, timeout):
        assert url == "http://localhost:11434/api/chat"
        assert json["model"] == "qwen2.5:3b"
        assert json["format"] == "json"
        assert json["stream"] is False
        assert timeout == 30
        return _fake_ollama_response(200, {"message": {"role": "assistant", "content": expected_text}})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = _call_ollama("system prompt", "user content")
    assert result == expected_text


def test_call_ollama_uses_configured_base_url(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://my-ollama-host:9999")
    monkeypatch.setattr(advisor_module.settings, "AI_MODEL", "qwen2.5:3b")

    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        return _fake_ollama_response(200, {"message": {"content": "[]"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    _call_ollama("s", "u")
    assert captured["url"] == "http://my-ollama-host:9999/api/chat"


def test_call_ollama_strips_trailing_slash_in_base_url(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434/")

    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        return _fake_ollama_response(200, {"message": {"content": "[]"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    _call_ollama("s", "u")
    assert captured["url"] == "http://localhost:11434/api/chat"


def test_call_ollama_empty_content_raises_provider_error(monkeypatch):
    def fake_post(url, json, timeout):
        return _fake_ollama_response(200, {"message": {"content": ""}})

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AIProviderError):
        _call_ollama("s", "u")


def test_call_ollama_missing_message_key_raises_provider_error(monkeypatch):
    def fake_post(url, json, timeout):
        return _fake_ollama_response(200, {"unexpected": "shape"})

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AIProviderError):
        _call_ollama("s", "u")


def test_call_ollama_http_error_raises_provider_error(monkeypatch):
    def fake_post(url, json, timeout):
        return _fake_ollama_response(500, {"error": "internal error detail"})

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AIProviderError) as exc_info:
        _call_ollama("s", "u")
    # The provider's raw error body must never leak into our exception message.
    assert "internal error detail" not in str(exc_info.value)


def test_call_ollama_timeout_raises_provider_error(monkeypatch):
    def fake_post(url, json, timeout):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(AIProviderError, match="timed out"):
        _call_ollama("s", "u")


def test_call_ollama_connection_failure_raises_provider_error(monkeypatch):
    """Simulates Ollama not running (connection refused) — must never crash
    the API, and must never leak the raw socket error or the configured URL."""
    def fake_post(url, json, timeout):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    with pytest.raises(AIProviderError) as exc_info:
        _call_ollama("s", "u")
    message = str(exc_info.value)
    assert "11434" not in message
    assert "Connection refused" not in message


def test_call_ollama_wrong_url_is_a_request_error(monkeypatch):
    """A misconfigured OLLAMA_BASE_URL manifests as a connection/DNS
    failure, handled the same safe way as Ollama simply not running."""
    def fake_post(url, json, timeout):
        raise httpx.ConnectError("Name or service not known")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://not-a-real-host:11434")

    with pytest.raises(AIProviderError):
        _call_ollama("s", "u")


# --- _call_provider dispatch --------------------------------------------

def test_call_provider_dispatches_to_ollama(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")

    def fake_post(url, json, timeout):
        return _fake_ollama_response(200, {"message": {"content": "[]"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert _call_provider("s", "u") == "[]"


# --- End-to-end through generate_recommendations() with Ollama -------------

def test_generate_recommendations_ollama_end_to_end_success(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")  # no key needed
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(advisor_module.settings, "AI_MODEL", "qwen2.5:3b")

    response_text = (
        '[{"problem": "High complexity", "severity": "HIGH", '
        '"explanation": "Complexity 24 across nested branches.", '
        '"recommendation": "Extract smaller methods.", "priority": 1}]'
    )

    def fake_post(url, json, timeout):
        return _fake_ollama_response(200, {"message": {"content": response_text}})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is True
    assert len(result.recommendations) == 1
    assert result.recommendations[0].severity == "HIGH"


def test_generate_recommendations_ollama_unavailable_returns_clean_state(monkeypatch):
    """The confirmed real-world case in this sandbox: Ollama is not
    running. Must degrade cleanly, never crash, never fabricate advice."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    def fake_post(url, json, timeout):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is False
    assert result.recommendations == []
    assert result.message is not None
    assert "refused" not in result.message.lower()
    assert "11434" not in result.message


def test_generate_recommendations_ollama_malformed_json_returns_clean_state(monkeypatch):
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    def fake_post(url, json, timeout):
        return _fake_ollama_response(200, {"message": {"content": "not valid json {{"}})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is False
    assert result.recommendations == []


def test_generate_recommendations_ollama_wrong_root_shape(monkeypatch):
    """A JSON object with no identifiable recommendation array anywhere in
    it (not even under a wrapper key) is genuinely unrecoverable and must
    still be caught by the normalization layer. Note: {"recommendations":
    [...]} specifically is now an ACCEPTED wrapper shape (see
    test_generate_recommendations_ollama_wrapped_object_is_accepted) —
    this test covers truly malformed output, not that shape."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    def fake_post(url, json, timeout):
        return _fake_ollama_response(200, {"message": {"content": '{"summary": "no issues found"}'}})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is False


def test_generate_recommendations_ollama_wrapped_object_is_accepted(monkeypatch):
    """Reproduces the exact real-world failure reported after Phase 6D:
    qwen2.5:3b (via Ollama's 'format: json') wrapping the recommendation
    array in an object instead of returning a bare array. This must now
    be accepted, with every entry still validated exactly as a bare array
    would be — not a relaxation of per-entry validation, only of where
    the array is located in the response."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    wrapped_response = (
        '{"recommendations": [{"problem": "High complexity", "severity": "HIGH", '
        '"explanation": "Nested branching increases path count.", '
        '"recommendation": "Extract helper functions.", "priority": 1}]}'
    )

    def fake_post(url, json, timeout):
        return _fake_ollama_response(200, {"message": {"content": wrapped_response}})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is True
    assert len(result.recommendations) == 1
    assert result.recommendations[0].problem == "High complexity"
    assert result.recommendations[0].severity == "HIGH"


def test_generate_recommendations_ollama_wrapped_empty_list_is_accepted(monkeypatch):
    """A wrapped-but-empty array ({"recommendations": []}) is a valid
    'no issues found' response, not a malformed one."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    def fake_post(url, json, timeout):
        return _fake_ollama_response(200, {"message": {"content": '{"recommendations": []}'}})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is True
    assert result.recommendations == []


def test_generate_recommendations_ollama_wrapped_under_alternate_key(monkeypatch):
    """A single unambiguous list-valued field under a different key (e.g.
    'items') is also safely unwrapped."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    wrapped_response = (
        '{"items": [{"problem": "P", "severity": "LOW", "explanation": "e", '
        '"recommendation": "r", "priority": 1}]}'
    )

    def fake_post(url, json, timeout):
        return _fake_ollama_response(200, {"message": {"content": wrapped_response}})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is True
    assert len(result.recommendations) == 1


def test_generate_recommendations_ollama_single_object_is_accepted(monkeypatch):
    """Reproduces the exact real-world qwen2.5:3b behavior confirmed on
    Windows: a single recommendation returned as a bare JSON object, not
    wrapped in any array at all. Must be accepted as a one-item result,
    with the same per-field validation as any other shape."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(advisor_module.settings, "AI_MODEL", "qwen2.5:3b")

    single_object_response = (
        '{"severity":"high","priority":1,"problem":"Test problem",'
        '"explanation":"Test explanation","recommendation":"Test recommendation"}'
    )

    def fake_post(url, json, timeout):
        return _fake_ollama_response(200, {"message": {"content": single_object_response}})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is True
    assert len(result.recommendations) == 1
    rec = result.recommendations[0]
    assert rec.problem == "Test problem"
    assert rec.severity == "HIGH"  # coerced to uppercase, matching existing severity handling
    assert rec.explanation == "Test explanation"
    assert rec.recommendation == "Test recommendation"
    assert rec.priority == 1


def test_generate_recommendations_arbitrary_object_without_recommendation_fields_still_fails(monkeypatch):
    """A single-object shape is only accepted when it actually looks like
    a recommendation. An arbitrary object missing problem/explanation/
    recommendation must still be safely rejected, not treated as a fake
    recommendation."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    def fake_post(url, json, timeout):
        return _fake_ollama_response(200, {"message": {"content": '{"summary": "no issues found", "status": "ok"}'}})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is False
    assert result.recommendations == []


def test_generate_recommendations_partial_single_object_still_fails(monkeypatch):
    """A single object missing even one of the three required content
    fields (here: no 'recommendation' field) must not be accepted as
    shape 3 -- it doesn't carry the full required structure."""
    monkeypatch.setattr(advisor_module.settings, "AI_PROVIDER", "ollama")
    monkeypatch.setattr(advisor_module.settings, "AI_API_KEY", "")
    monkeypatch.setattr(advisor_module.settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    def fake_post(url, json, timeout):
        return _fake_ollama_response(
            200, {"message": {"content": '{"problem": "P", "explanation": "e"}'}}
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    result = generate_recommendations({"project": {}, "modules": []})
    assert result.available is False
