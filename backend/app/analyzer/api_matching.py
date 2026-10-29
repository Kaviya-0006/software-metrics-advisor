"""
Project Architecture Intelligence — Phase 8C (part 2): frontend ↔ backend mapping.

Joins the two sides of a full-stack project: the HTTP calls the frontend makes
(`api_calls.py`) and the endpoints the backend declares (`api_endpoints.py`).

These are **static-analysis findings, never runtime facts.** A frontend call
with no match is a *potential integration issue*; an endpoint with no match is
a *potentially unused* endpoint ("could not find a matching frontend consumer"
— it may well be used by a mobile app, another service, a script or a
dynamically built URL that static analysis cannot see).

Result categories
-----------------
MATCHED                  a call and an endpoint agree on method and path shape
UNMATCHED_FRONTEND_CALL  no backend endpoint matches the call
UNUSED_BACKEND_ENDPOINT  no frontend call matches the endpoint
UNCERTAIN                the evidence is too weak to call it either way:
                         an external-host URL, an unidentified client wrapper,
                         a project with no detected backend endpoints (or no
                         detected frontend calls at all)

Matching rules
--------------
* **Method:** equal, case-insensitively. An endpoint declared for ``ANY``
  method (Spring ``@RequestMapping`` without ``method``) matches every method.
* **Path shape:** both paths are split into segments and every explicit
  parameter form is normalized to ``{}``: Express ``:id``, ``{id}`` /
  ``{id:int}`` (FastAPI, Spring), Flask ``<id>`` / ``<int:id>``, and a call's
  ``${id}`` (already ``{}``). Static segments are compared exactly and are never
  turned into parameters.
* **A literal segment may satisfy an endpoint's parameter segment**
  (call ``/users/123`` ↔ endpoint ``/users/{id}``), reported as
  ``parameter-vs-static`` at LOW confidence; the reverse (a call parameter
  against a static endpoint segment) does not match.
* A call ``/api/users`` does **not** match an endpoint ``/users``: the missing
  prefix is only accepted when mount-prefix analysis (Phase 8B) proved it. The
  unmatched finding says so, as a hint.
* Wildcard endpoints (Express ``*``) are not treated as matching everything.

Match confidence
----------------
Start from the quality of the match — exact path text HIGH, normalization
required MEDIUM, parameter-vs-static LOW — then cap it:
MEDIUM unless the layers are clear (call in the frontend layer, endpoint in the
backend/api layer); MEDIUM for an ``ANY`` endpoint; LOW for an external host;
never above the confidence of the call or of the endpoint themselves. A
finding about the *absence* of a match is never above MEDIUM (LOW when a
backend endpoint exists without the call's leading prefix, which suggests an
unresolved mount rather than a real gap).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.analyzer.api_calls import LIBRARY_UNKNOWN, ApiCall, ApiCallSummary, detect_api_calls
from app.analyzer.api_endpoints import METHOD_ANY, ApiEndpoint, ApiEndpointSummary, detect_api_endpoints
from app.analyzer.architecture import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    LAYER_API,
    LAYER_BACKEND,
    LAYER_FRONTEND,
    ArchitectureSummary,
)
from app.analyzer.parser import ParsedModule

STATUS_MATCHED = "MATCHED"
STATUS_UNMATCHED_CALL = "UNMATCHED_FRONTEND_CALL"
STATUS_UNUSED_ENDPOINT = "UNUSED_BACKEND_ENDPOINT"
STATUS_UNCERTAIN = "UNCERTAIN"
_STATUS_ORDER = (STATUS_MATCHED, STATUS_UNMATCHED_CALL, STATUS_UNUSED_ENDPOINT, STATUS_UNCERTAIN)

QUALITY_EXACT = "exact"
QUALITY_NORMALIZED = "normalized"
QUALITY_PARAMETER_VS_STATIC = "parameter-vs-static"
_QUALITY_RANK = {QUALITY_EXACT: 3, QUALITY_NORMALIZED: 2, QUALITY_PARAMETER_VS_STATIC: 1}
_QUALITY_CONFIDENCE = {
    QUALITY_EXACT: CONFIDENCE_HIGH,
    QUALITY_NORMALIZED: CONFIDENCE_MEDIUM,
    QUALITY_PARAMETER_VS_STATIC: CONFIDENCE_LOW,
}
_CONFIDENCE_RANK = {CONFIDENCE_HIGH: 3, CONFIDENCE_MEDIUM: 2, CONFIDENCE_LOW: 1}

NOTE = (
    "Static-analysis findings: unmatched calls are potential integration issues and unmatched "
    "endpoints are potentially unused, not runtime facts."
)

_TEMPLATE_PARAM = re.compile(r"\$\{[^{}]*\}")
_BRACE_PARAM = re.compile(r"\{[^{}]*\}")
_ANGLE_PARAM = re.compile(r"<[^<>]*>")


# --------------------------------------------------------------------------
# Path shapes
# --------------------------------------------------------------------------


def _segment_shape(segment: str) -> str:
    if segment.startswith(":") and len(segment) > 1:
        return "{}"  # Express `:id`, `:id?`, `:id(\\d+)`
    segment = _TEMPLATE_PARAM.sub("{}", segment)
    segment = _BRACE_PARAM.sub("{}", segment)  # `{id}`, `{id:int}`, `{}`
    return _ANGLE_PARAM.sub("{}", segment)  # Flask `<id>`, `<int:id>`


def path_shape(path: str) -> tuple[str, ...]:
    """The segments of `path` with every explicit parameter form turned into `{}`."""
    return tuple(_segment_shape(s) for s in path.split("?")[0].split("/") if s)


def _min_confidence(*levels: str) -> str:
    return min(levels, key=lambda level: _CONFIDENCE_RANK[level])


def _cap(level: str, ceiling: str) -> str:
    return _min_confidence(level, ceiling)


# --------------------------------------------------------------------------
# Result model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiMatchResult:
    """One finding, with the evidence on each side that supports it."""

    status: str
    method: str
    path: str
    confidence: str
    reason: str
    call: ApiCall | None = None
    endpoint: ApiEndpoint | None = None
    match_quality: str | None = None  # MATCHED only: exact | normalized | parameter-vs-static

    def to_dict(self) -> dict[str, Any]:
        frontend = backend = None
        if self.call is not None:
            frontend = {
                "file": self.call.source_file,
                "line": self.call.line_number,
                "call": f"{self.call.method} {self.call.path}",
                "library": self.call.library,
                "layer": self.call.layer,
            }
        if self.endpoint is not None:
            backend = {
                "file": self.endpoint.source_file,
                "line": self.endpoint.line_number,
                "endpoint": f"{self.endpoint.method} {self.endpoint.path}",
                "framework": self.endpoint.framework,
                "layer": self.endpoint.layer,
            }
        return {
            "status": self.status,
            "method": self.method,
            "path": self.path,
            "confidence": self.confidence,
            "match_quality": self.match_quality,
            "reason": self.reason,
            "frontend": frontend,
            "backend": backend,
        }


@dataclass
class ApiMappingReport:
    """Every finding of one project, plus the detected inputs they came from."""

    results: list[ApiMatchResult] = field(default_factory=list)
    total_calls: int = 0
    total_endpoints: int = 0
    call_summary: ApiCallSummary | None = None
    endpoint_summary: ApiEndpointSummary | None = None

    def _with_status(self, status: str) -> list[ApiMatchResult]:
        return [r for r in self.results if r.status == status]

    @property
    def matched(self) -> list[ApiMatchResult]:
        return self._with_status(STATUS_MATCHED)

    @property
    def unmatched_frontend_calls(self) -> list[ApiMatchResult]:
        return self._with_status(STATUS_UNMATCHED_CALL)

    @property
    def unused_backend_endpoints(self) -> list[ApiMatchResult]:
        return self._with_status(STATUS_UNUSED_ENDPOINT)

    @property
    def uncertain(self) -> list[ApiMatchResult]:
        return self._with_status(STATUS_UNCERTAIN)

    @property
    def by_status(self) -> dict[str, int]:
        counts = Counter(r.status for r in self.results)
        return {status: counts[status] for status in _STATUS_ORDER if counts[status]}

    @property
    def matched_by_confidence(self) -> dict[str, int]:
        counts = Counter(r.confidence for r in self.matched)
        return {level: counts[level] for level in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW) if counts[level]}

    def to_dict(self, include_results: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "total_calls": self.total_calls,
            "total_endpoints": self.total_endpoints,
            "matched": len(self.matched),
            "unmatched_frontend_calls": len(self.unmatched_frontend_calls),
            "unused_backend_endpoints": len(self.unused_backend_endpoints),
            "uncertain": len(self.uncertain),
            "matched_by_confidence": self.matched_by_confidence,
            "note": NOTE,
        }
        if include_results:
            data["results"] = [r.to_dict() for r in self.results]
        return data


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def _compare(call: ApiCall, endpoint: ApiEndpoint) -> str | None:
    """Quality of the match between one call and one endpoint, or None."""
    if endpoint.method != METHOD_ANY and endpoint.method != call.method:
        return None
    call_shape, endpoint_shape = path_shape(call.path), path_shape(endpoint.path)
    if len(call_shape) != len(endpoint_shape):
        return None
    literal_for_parameter = False
    for call_segment, endpoint_segment in zip(call_shape, endpoint_shape):
        if call_segment == endpoint_segment:
            continue
        if endpoint_segment == "{}" and "{}" not in call_segment:
            literal_for_parameter = True  # `/users/123` against `/users/{id}`
            continue
        return None
    if literal_for_parameter:
        return QUALITY_PARAMETER_VS_STATIC
    same_text = call.path.rstrip("/") == endpoint.path.rstrip("/")
    return QUALITY_EXACT if same_text else QUALITY_NORMALIZED


def _match_confidence(call: ApiCall, endpoint: ApiEndpoint, quality: str) -> str:
    level = _QUALITY_CONFIDENCE[quality]
    clear_layers = call.layer == LAYER_FRONTEND and endpoint.layer in (LAYER_BACKEND, LAYER_API)
    if not clear_layers:
        level = _cap(level, CONFIDENCE_MEDIUM)  # weaker architecture evidence
    if endpoint.method == METHOD_ANY:
        level = _cap(level, CONFIDENCE_MEDIUM)
    if call.external:
        level = CONFIDENCE_LOW
    return _min_confidence(level, call.confidence, endpoint.confidence)


def _unmatched_hint(call: ApiCall, endpoints: list[ApiEndpoint]) -> tuple[str, bool]:
    """-> (extra sentence for an unmatched call, is_prefix_hint)."""
    call_shape = path_shape(call.path)
    other_methods = sorted(
        {
            e.method
            for e in endpoints
            if e.method not in (METHOD_ANY, call.method) and path_shape(e.path) == call_shape
        }
    )
    if other_methods:
        return (f" The backend declares this path only for {', '.join(other_methods)}.", False)
    for endpoint in endpoints:
        shape = path_shape(endpoint.path)
        if (
            endpoint.method in (METHOD_ANY, call.method)
            and 0 < len(shape) < len(call_shape)
            and call_shape[len(call_shape) - len(shape) :] == shape
        ):
            lead = "/" + "/".join(call_shape[: len(call_shape) - len(shape)])
            return (
                f" The backend declares {endpoint.method} {endpoint.path} without the leading {lead}; "
                "that prefix may come from a mount that could not be resolved statically.",
                True,
            )
    return ("", False)


def _as_list(items: Any, summary_type: type, attribute: str) -> list:
    if isinstance(items, summary_type):
        return list(getattr(items, attribute))
    return list(items)


def match_api_calls(
    calls: ApiCallSummary | Iterable[ApiCall],
    endpoints: ApiEndpointSummary | Iterable[ApiEndpoint],
) -> ApiMappingReport:
    """Match frontend calls against backend endpoints (see the module docstring)."""
    call_list = _as_list(calls, ApiCallSummary, "calls")
    endpoint_list = _as_list(endpoints, ApiEndpointSummary, "endpoints")
    report = ApiMappingReport(total_calls=len(call_list), total_endpoints=len(endpoint_list))
    used: set[int] = set()
    results: list[ApiMatchResult] = []

    for call in call_list:
        candidates = []
        for index, endpoint in enumerate(endpoint_list):
            quality = _compare(call, endpoint)
            if quality is not None:
                candidates.append((_QUALITY_RANK[quality], endpoint.method != METHOD_ANY, _CONFIDENCE_RANK[endpoint.confidence], index, quality))
        if candidates:
            best_key = max(c[:3] for c in candidates)
            ranked = [c for c in candidates if c[:3] == best_key]
            for c in ranked:
                used.add(c[3])  # equally good duplicates are all "consumed"
            index, quality = ranked[0][3], ranked[0][4]
            endpoint = endpoint_list[index]
            results.append(
                ApiMatchResult(
                    status=STATUS_MATCHED,
                    method=call.method,
                    path=call.path,
                    confidence=_match_confidence(call, endpoint, quality),
                    reason=f"Frontend {call.method} {call.path} matches backend {endpoint.method} {endpoint.path} ({quality}).",
                    call=call,
                    endpoint=endpoint,
                    match_quality=quality,
                )
            )
            continue

        label = f"{call.method} {call.path}"
        if not endpoint_list:
            results.append(ApiMatchResult(
                STATUS_UNCERTAIN, call.method, call.path, CONFIDENCE_LOW,
                f"Could not assess {label}: no backend endpoints were detected in the analyzed project.", call=call))
        elif call.external:
            results.append(ApiMatchResult(
                STATUS_UNCERTAIN, call.method, call.path, CONFIDENCE_LOW,
                f"Could not assess {label}: the call targets an absolute URL on another host, "
                "which may be a third-party service rather than this project's backend.", call=call))
        elif call.library == LIBRARY_UNKNOWN:
            results.append(ApiMatchResult(
                STATUS_UNCERTAIN, call.method, call.path, CONFIDENCE_LOW,
                f"Could not assess {label}: the call goes through a client wrapper that could not be "
                "identified as an HTTP client.", call=call))
        else:
            hint, prefix_hint = _unmatched_hint(call, endpoint_list)
            results.append(ApiMatchResult(
                STATUS_UNMATCHED_CALL, call.method, call.path,
                CONFIDENCE_LOW if prefix_hint else _cap(call.confidence, CONFIDENCE_MEDIUM),
                f"Potential integration issue: no backend endpoint matching {label} was found in the "
                f"analyzed code.{hint}", call=call))

    for index, endpoint in enumerate(endpoint_list):
        if index in used:
            continue
        label = f"{endpoint.method} {endpoint.path}"
        if not call_list:
            results.append(ApiMatchResult(
                STATUS_UNCERTAIN, endpoint.method, endpoint.path, CONFIDENCE_LOW,
                f"Could not assess {label}: no frontend API calls were detected in the analyzed project, "
                "so its consumers (if any) are external or not statically visible.", endpoint=endpoint))
        else:
            results.append(ApiMatchResult(
                STATUS_UNUSED_ENDPOINT, endpoint.method, endpoint.path, _cap(endpoint.confidence, CONFIDENCE_MEDIUM),
                f"Potentially unused backend endpoint: could not find a matching frontend consumer for {label}. "
                "It may still be used by clients outside the analyzed code.", endpoint=endpoint))

    rank = {status: i for i, status in enumerate(_STATUS_ORDER)}

    def location(result: ApiMatchResult) -> tuple:
        side = result.call or result.endpoint
        return (rank[result.status], side.source_file, side.line_number, result.method, result.path)

    report.results = sorted(results, key=location)
    return report


def analyze_api_mapping(
    parsed_modules: Iterable[ParsedModule],
    architecture: ArchitectureSummary | None = None,
) -> ApiMappingReport:
    """
    End to end: detect the backend endpoints and the frontend calls of a
    project (the same Phase 8A architecture information filters both) and match
    them. `parsed_modules` is typically
    ``analyze_project(root, retain_parsed=True).parsed_modules``.
    """
    modules = list(parsed_modules)
    endpoint_summary = detect_api_endpoints(modules, architecture)
    call_summary = detect_api_calls(modules, architecture)
    report = match_api_calls(call_summary, endpoint_summary)
    report.call_summary = call_summary
    report.endpoint_summary = endpoint_summary
    return report
