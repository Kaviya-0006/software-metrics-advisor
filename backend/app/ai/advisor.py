"""
AI Advisor.

Responsible for exactly one thing: turning already-persisted, already-
computed project/module data (metrics, health score, risk) into plain-
language explanations and recommendations via an LLM. This module:

  - NEVER computes a metric, health score, or risk score — see
    build_ai_context(), which only reads values already passed in.
  - NEVER sends source code to the provider — only the structured
    numeric/textual context built here.
  - Always degrades to a clean "unavailable" result rather than raising,
    if the provider isn't configured, fails, times out, or returns
    something that can't be safely parsed. The rest of the application
    (upload, metrics, health score, risk detection, dashboard APIs) does
    not depend on this module at all and is unaffected if it's disabled.

Provider call is isolated behind `_call_provider` specifically so tests
can monkeypatch/mock it without making a real network request.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import httpx

from app.config import settings
from app.ai.prompts import SYSTEM_PROMPT

logger = logging.getLogger("software_metrics_advisor.ai")

_VALID_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
_MAX_RECOMMENDATIONS = 8


class AIProviderError(Exception):
    """Raised for any provider-side failure (network, auth, HTTP status).
    Never includes the API key or raw provider response in its message —
    callers must not surface str(exc) directly to the API client."""


@dataclass
class Recommendation:
    problem: str
    severity: str
    explanation: str
    recommendation: str
    priority: int
    file_path: str | None = None
    official_risk: str | None = None
    evidence: dict = field(default_factory=dict)
    provenance: str = "AI advisory"
    disagreement: str | None = None

    def to_dict(self) -> dict:
        return {
            "problem": self.problem,
            "severity": self.severity,
            "explanation": self.explanation,
            "recommendation": self.recommendation,
            "priority": self.priority,
            "file_path": self.file_path,
            "official_risk": self.official_risk,
            "evidence": self.evidence,
            "provenance": self.provenance,
            "disagreement": self.disagreement,
        }


@dataclass
class AdvisorResult:
    available: bool
    recommendations: list[Recommendation] = field(default_factory=list)
    message: str | None = None

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "message": self.message,
        }


def is_ai_configured() -> bool:
    """
    Whether the AI Advisor can be called right now, given the current
    provider selection. This is provider-specific rather than a single
    blanket "is there an API key" check:

      - "anthropic": needs a real API key (a hosted, authenticated API).
      - "ollama": needs a base URL — which always has a sensible default
        (http://localhost:11434) — but NOT an API key. Ollama is a local,
        unauthenticated server by design; requiring a key for it would
        make a correctly-configured local setup report as "not
        configured", which is exactly the bug this function used to have
        before Phase 6B.
      - anything else (including "none", the default): not configured.
    """
    if settings.AI_PROVIDER == "anthropic":
        return bool(settings.AI_API_KEY)
    if settings.AI_PROVIDER == "ollama":
        return bool(settings.OLLAMA_BASE_URL)
    return False


# ---------------------------------------------------------------------------
# Context construction — pure data assembly, no computation
# ---------------------------------------------------------------------------


def build_ai_context(project_info: dict, modules_info: list[dict]) -> dict:
    """
    Build the structured JSON context sent to the AI. Every value here is
    read from already-persisted Project/Module data (see app/api/projects.py
    for how project_info/modules_info are assembled from the database) —
    nothing is recalculated. Null values are passed through as null, not
    substituted with a guess or a zero.

    `project_info` keys expected: name, health_score, health_status,
    category_scores (dict), summary (dict with files/classes/functions/total_loc).

    `modules_info` is a list of dicts, each combining one module's raw
    metrics (LOC, complexity, maintainability, coupling, cohesion, DIT,
    duplication, test_coverage, documentation_percentage — all as already
    persisted) with its risk_score/risk_level/risk_factors.
    """
    return {
        "project": {
            "name": project_info.get("name"),
            "health_score": project_info.get("health_score"),
            "health_status": project_info.get("health_status"),
            "category_scores": project_info.get("category_scores"),
            "summary": project_info.get("summary"),
        },
        "modules": [
            {
                "file_name": m.get("file_name"),
                "file_path": m.get("file_path"),
                "language": m.get("language"),
                "loc": m.get("loc"),
                "classes": m.get("classes"),
                "functions": m.get("functions"),
                "complexity": m.get("complexity"),
                "maintainability_index": m.get("maintainability_index"),
                "coupling": m.get("coupling"),
                "cohesion": m.get("cohesion"),
                "depth_of_inheritance": m.get("depth_of_inheritance"),
                "duplication_percentage": m.get("duplication_percentage"),
                "test_coverage": m.get("test_coverage"),
                "documentation_percentage": m.get("documentation_percentage"),
                "risk_score": m.get("risk_score"),
                "risk_level": m.get("risk_level"),
                "risk_factors": m.get("risk_factors") or [],
            }
            for m in modules_info
        ],
    }


# ---------------------------------------------------------------------------
# Provider call
# ---------------------------------------------------------------------------


def _call_anthropic(system_prompt: str, user_content: str) -> str:
    """Call the Anthropic Messages API and return the raw text of the
    model's reply. Raises AIProviderError on any failure — never returns
    partial/garbage data silently."""
    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.AI_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.AI_MODEL,
                "max_tokens": 2000,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_content}],
            },
            timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise AIProviderError("AI provider request timed out.") from exc
    except httpx.HTTPStatusError as exc:
        # Deliberately do not include exc's response body — provider error
        # bodies can echo request details back.
        raise AIProviderError(
            f"AI provider returned HTTP {exc.response.status_code}."
        ) from exc
    except httpx.RequestError as exc:
        raise AIProviderError("Could not reach the AI provider.") from exc

    try:
        body = response.json()
        return body["content"][0]["text"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIProviderError("AI provider returned an unexpected response shape.") from exc


def _call_ollama(system_prompt: str, user_content: str) -> str:
    """
    Call a locally-running Ollama server's chat API and return the raw
    text of the model's reply. Ollama is unauthenticated (no API key) —
    only a reachable base URL and an already-pulled model are required.
    Raises AIProviderError on any failure, exactly like _call_anthropic,
    so the caller's fallback handling is identical regardless of provider.
    """
    url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    try:
        response = httpx.post(
            url,
            json={
                "model": settings.AI_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                # Ollama's basic JSON mode: constrains the model to emit
                # syntactically valid JSON. This is a best-effort guarantee
                # from the model, not a schema validator — the
                # _normalize_recommendations() validation layer below
                # remains the actual safety boundary regardless.
                "format": "json",
                "stream": False,
            },
            timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise AIProviderError("AI provider request timed out.") from exc
    except httpx.HTTPStatusError as exc:
        raise AIProviderError(
            f"AI provider returned HTTP {exc.response.status_code}."
        ) from exc
    except httpx.RequestError as exc:
        # Covers connection refused (Ollama not running), DNS/host
        # resolution failure (wrong OLLAMA_BASE_URL), and similar —
        # deliberately a single generic message, never the raw URL or
        # underlying socket error, which could reveal internal network
        # details.
        raise AIProviderError("Could not reach the AI provider.") from exc

    try:
        body = response.json()
        content = body["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("AI provider returned an empty response.")
        return content
    except (KeyError, TypeError, ValueError) as exc:
        raise AIProviderError("AI provider returned an unexpected response shape.") from exc


def _call_provider(system_prompt: str, user_content: str) -> str:
    """Dispatch to the configured provider. Isolated as its own function
    so tests can monkeypatch `app.ai.advisor._call_provider` directly
    instead of mocking httpx internals."""
    if settings.AI_PROVIDER == "anthropic":
        return _call_anthropic(system_prompt, user_content)
    if settings.AI_PROVIDER == "ollama":
        return _call_ollama(system_prompt, user_content)
    raise AIProviderError(f"Unsupported AI_PROVIDER: {settings.AI_PROVIDER!r}")


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


def _coerce_severity(value) -> str:
    if isinstance(value, str) and value.strip().upper() in _VALID_SEVERITIES:
        return value.strip().upper()
    return "MEDIUM"  # safe default for the AI's own judgment field, not a metric


def _coerce_priority(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _extract_recommendation_list(parsed):
    """
    Locate the recommendation array within the parsed JSON, accepting
    three shapes:

      1. A bare JSON array (the primary shape the prompt asks for):
         [{"problem": ..., ...}, ...]
      2. A JSON object wrapping that same array under a single key, e.g.
         {"recommendations": [{"problem": ..., ...}, ...]}
      3. A single recommendation object, not wrapped in a list at all:
         {"problem": ..., "severity": ..., "explanation": ...,
          "recommendation": ..., "priority": ...}

    Shape 2 is accepted because smaller local models (observed with
    qwen2.5:3b via Ollama's "format": "json" mode) frequently wrap the
    array in an object despite explicit instructions to return a bare
    array. Shape 3 is accepted for the same reason, taken further: when
    the model only has one recommendation to give, it may collapse
    straight to that single object instead of a one-item array or a
    one-item wrapped array (confirmed against a real qwen2.5:3b response
    on Windows — see tests/test_ollama_provider.py). Ollama's JSON mode
    only guarantees syntactically valid JSON, not any particular root
    shape or nesting depth.

    None of this is schema relaxation: whichever shape is found, every
    entry still goes through the exact same per-field validation in
    _normalize_recommendations() below. A single object is only ever
    treated as shape 3 if it actually carries the required recommendation
    fields (problem/explanation/recommendation) — an arbitrary JSON
    object without that shape (e.g. {"summary": "no issues found"}) is
    still correctly rejected, not silently wrapped as a fake
    recommendation.
    """
    if isinstance(parsed, list):
        return parsed

    if isinstance(parsed, dict):
        # Prefer the field name the prompt/response schema actually uses.
        preferred = parsed.get("recommendations")
        if isinstance(preferred, list):
            return preferred

        # A handful of other common wrapper keys models sometimes choose.
        for key in ("results", "items", "data"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value

        # Last resort: an object with exactly one list-valued field is
        # unambiguous enough to safely unwrap.
        list_values = [v for v in parsed.values() if isinstance(v, list)]
        if len(list_values) == 1:
            return list_values[0]

        # Shape 3: the object itself looks like a single recommendation —
        # require all three content fields to be present as a reasonably
        # strong signal, so a genuinely unrelated object is still rejected
        # below rather than being treated as one bogus "recommendation."
        required_fields = {"problem", "explanation", "recommendation"}
        if required_fields.issubset(parsed.keys()):
            return [parsed]

    raise ValueError("Expected a JSON array of recommendations.")


def _normalize_recommendations(parsed) -> list[Recommendation]:
    """
    Validate and normalize the AI's parsed JSON output. Individual
    malformed entries within an otherwise-valid list are skipped (a
    partial response is still useful); a completely wrong root shape
    raises, so the caller can fall back to a clean unavailable state
    instead of returning garbage to the client.
    """
    items = _extract_recommendation_list(parsed)

    recommendations: list[Recommendation] = []
    for index, item in enumerate(items[:_MAX_RECOMMENDATIONS]):
        if not isinstance(item, dict):
            continue
        problem = item.get("problem")
        explanation = item.get("explanation")
        recommendation_text = item.get("recommendation")
        if not (isinstance(problem, str) and problem.strip()):
            continue
        if not (isinstance(explanation, str) and explanation.strip()):
            continue
        if not (isinstance(recommendation_text, str) and recommendation_text.strip()):
            continue

        recommendations.append(
            Recommendation(
                problem=problem.strip(),
                severity=_coerce_severity(item.get("severity")),
                explanation=explanation.strip(),
                recommendation=recommendation_text.strip(),
                priority=_coerce_priority(item.get("priority"), fallback=index + 1),
            )
        )

    return recommendations



# Ground model suggestions in known, persisted metrics.  Unknown or ambiguous
# module references are discarded instead of allowing hallucinated evidence.
_EVIDENCE_FIELDS = ("complexity", "maintainability_index", "coupling", "cohesion",
                    "duplication_percentage", "documentation_percentage", "risk_score")

def _ground_recommendations(recommendations: list[Recommendation], context: dict) -> list[Recommendation]:
    """Attach every AI recommendation to exactly one persisted module.

    Small local models often mention only the filename (for example
    ``AuthService.java``) even though the prompt asks for the full path.
    Accept an exact full-path match first, then a unique basename match.
    Never accept an ambiguous basename: that would weaken the evidence
    boundary and could attach advice to the wrong file.
    """
    modules = context.get("modules") or []
    grounded = []
    for rec in recommendations:
        text = " ".join(
            value for value in (rec.file_path, rec.problem, rec.explanation, rec.recommendation)
            if isinstance(value, str)
        ).lower()
        matches = []
        for m in modules:
            path = m.get("file_path") or m.get("file_name")
            if not path:
                continue
            full = str(path).lower()
            name = str(m.get("file_name") or Path(str(path)).name).lower()
            if full in text or (name and name in text):
                matches.append(m)

        # A basename is only safe when it identifies one module.
        if len(matches) != 1:
            continue

        module = matches[0]
        rec.file_path = module.get("file_path") or module.get("file_name")
        rec.official_risk = module.get("risk_level")
        rec.evidence = {key: module[key] for key in _EVIDENCE_FIELDS if module.get(key) is not None}
        rec.provenance = "Persisted static metrics; AI-generated interpretation"
        if rec.official_risk and rec.severity != rec.official_risk:
            rec.disagreement = ("Official deterministic risk is " + rec.official_risk +
                "; AI advisory concern is " + rec.severity +
                ". The official risk has not changed; review the listed evidence.")
        grounded.append(rec)
    return grounded


def _fallback(context: dict, reason: str) -> AdvisorResult:
    """Deterministic, offline guidance; never invents metrics or file names."""
    ranked = sorted(context.get("modules") or [],
                    key=lambda m: (m.get("risk_score") is not None, m.get("risk_score") or 0), reverse=True)
    recommendations = []
    for module in ranked:
        path = module.get("file_path") or module.get("file_name")
        if not path:
            continue
        evidence = {key: module[key] for key in _EVIDENCE_FIELDS if module.get(key) is not None}
        if not evidence:
            continue
        risk = module.get("risk_level") or "LOW"
        complexity = module.get("complexity")
        if isinstance(complexity, dict):
            complexity_value = complexity.get("total")
        else:
            complexity_value = complexity
        if complexity_value is not None and complexity_value >= 10:
            action = "Review complex decision paths and extract smaller, independently testable functions."
            problem = "Elevated cyclomatic complexity"
        elif module.get("maintainability_index") is not None and module["maintainability_index"] < 65:
            action = "Review large or tightly coupled routines and simplify responsibilities."
            problem = "Low maintainability index"
        elif module.get("duplication_percentage") is not None and module["duplication_percentage"] >= 10:
            action = "Inspect repeated logic and extract shared utilities where appropriate."
            problem = "Repeated code"
        elif risk in ("HIGH", "CRITICAL"):
            action = "Review the recorded risk factors and prioritize the most affected functions."
            problem = "Elevated module risk"
        else:
            continue
        recommendations.append(Recommendation(problem=problem, severity=risk,
            explanation=f"{path}: review the measured values shown in the evidence panel.",
            recommendation=action, priority=len(recommendations)+1,
            file_path=path, official_risk=risk, evidence=evidence,
            provenance="Deterministic fallback from persisted metrics"))
        if len(recommendations) == _MAX_RECOMMENDATIONS:
            break
    return AdvisorResult(available=False, recommendations=recommendations, message=reason)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate_recommendations(context: dict) -> AdvisorResult:
    """
    Given a context built by build_ai_context(), call the AI provider and
    return a normalized AdvisorResult. Never raises — every failure mode
    (not configured, provider error, malformed output) becomes a clean
    AdvisorResult(available=False, message=...) instead.
    """
    if not is_ai_configured():
        return _fallback(context, "AI provider is not configured; showing metric-based guidance.")

    try:
        raw_text = _call_provider(SYSTEM_PROMPT, json.dumps(context))
    except AIProviderError as exc:
        # Log the real reason server-side; never forward provider/network
        # details (or the API key, which never appears in this message in
        # the first place) to the API client.
        logger.warning("AI provider call failed: %s", exc)
        return _fallback(context, "AI provider unavailable; showing metric-based guidance.")

    try:
        parsed = json.loads(raw_text)
        recommendations = _normalize_recommendations(parsed)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("AI provider returned unparseable output: %s", exc)
        return _fallback(context, "Invalid AI response; showing metric-based guidance.")

    grounded = _ground_recommendations(recommendations, context)
    if not grounded:
        # A valid-but-empty or ungroundable LLM response must not erase useful
        # deterministic guidance. Reuse the same persisted-metric fallback
        # used for provider failures, but keep the provider status visible.
        fallback = _fallback(context, "AI response contained no evidence-backed recommendations; showing metric-based guidance.")
        if fallback.recommendations:
            return fallback
    return AdvisorResult(available=True, recommendations=grounded, message=None)
