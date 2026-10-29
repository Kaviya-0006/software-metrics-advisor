"""
Project Architecture Intelligence — Phase 8D: the assembled architecture report.

Phases 8A–8C produced four independent analyzers:

* `architecture.analyze_architecture`     – layer classification (8A)
* `api_endpoints.detect_api_endpoints`    – backend endpoint declarations (8B)
* `api_calls.detect_api_calls`            – frontend HTTP calls (8C)
* `api_matching.analyze_api_mapping`      – call <-> endpoint matching (8C)

This module **only assembles** their output into one JSON-serializable report
that can be persisted and served. It performs no detection of its own: every
layer, endpoint, call, match and finding in the report was produced by those
modules, and the diagram is a direct projection of the same results.

Report sections (``schema_version`` 1)
--------------------------------------
``project_type``       full-stack | frontend-only | backend-only | undetermined,
                       with ``project_type_basis`` explaining the evidence
``confidence``         the 8A project-level confidence
``layers``             one entry per layer that received files (incl. unknown),
                       with files, LOC, language counts and confidence
``files``              per-file layer classification with its evidence strings
``endpoints``/``calls`` every detected endpoint / call with file + line
``api_mapping``        counts, plus matched / unmatched / unused / uncertain
                       findings, each with source-file and line evidence
``uncertainties``      everything the analysis could not decide, in one list
``diagram``            ``{nodes, edges}`` drawn only from the results above
``coverage``           what the endpoint and call detectors did / did not scan

Static-analysis honesty
-----------------------
All findings are static-analysis observations, never runtime facts. An
unmatched call is a *potential* integration issue; an unmatched endpoint is
*potentially* unused. The diagram draws an edge only where a frontend call was
actually matched to a backend endpoint — relationships the analyzers do not
look at (for example database access) are not drawn.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from app.analyzer.api_matching import NOTE as API_MATCHING_NOTE
from app.analyzer.api_matching import (
    ApiMappingReport,
    ApiMatchResult,
    analyze_api_mapping,
)
from app.analyzer.architecture import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    LAYER_API,
    LAYER_BACKEND,
    LAYER_FRONTEND,
    LAYER_ORDER,
    LAYER_UNKNOWN,
    ArchitectureSummary,
    analyze_architecture,
)
from app.analyzer.parser import ParsedModule

SCHEMA_VERSION = 1

PROJECT_FULL_STACK = "full-stack"
PROJECT_FRONTEND_ONLY = "frontend-only"
PROJECT_BACKEND_ONLY = "backend-only"
PROJECT_UNDETERMINED = "undetermined"

_CONFIDENCE_RANK = {CONFIDENCE_LOW: 1, CONFIDENCE_MEDIUM: 2, CONFIDENCE_HIGH: 3}

DIAGRAM_NOTE = (
    "Layers and file counts come from path/name evidence; an arrow is drawn only "
    "where static analysis matched a frontend API call to a backend endpoint. "
    "Other relationships (for example database access) are not analyzed, so no "
    "arrow is drawn for them."
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _min_confidence(levels: Iterable[str]) -> str:
    ranked = [lvl for lvl in levels if lvl in _CONFIDENCE_RANK]
    if not ranked:
        return CONFIDENCE_LOW
    return min(ranked, key=_CONFIDENCE_RANK.__getitem__)


def _layer_dict(summary: ArchitectureSummary) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = []
    for layer in LAYER_ORDER:
        item = summary.layers.get(layer)
        if item is None:
            continue
        data = item.to_dict()
        data["layer"] = layer
        data["detected"] = layer != LAYER_UNKNOWN
        layers.append(data)
    return layers


def _result_dict(result: ApiMatchResult) -> dict[str, Any]:
    """An API finding, with flat file/line evidence lists for the UI."""
    data = result.to_dict()
    evidence: list[dict[str, Any]] = []
    if result.call is not None:
        evidence.append(
            {
                "side": "frontend",
                "file": result.call.source_file,
                "line": result.call.line_number,
                "detail": f"{result.call.method} {result.call.path} via {result.call.library}",
            }
        )
    if result.endpoint is not None:
        evidence.append(
            {
                "side": "backend",
                "file": result.endpoint.source_file,
                "line": result.endpoint.line_number,
                "detail": f"{result.endpoint.method} {result.endpoint.path} ({result.endpoint.framework})",
            }
        )
    data["evidence"] = evidence
    return data


def _project_type(
    summary: ArchitectureSummary, report: ApiMappingReport
) -> tuple[str, str]:
    """
    Decide frontend-only / backend-only / full-stack from *evidence only*:
    a detected frontend (or backend/api) layer, or detected API calls (or
    endpoints). With neither side evidenced the answer is ``undetermined``.
    """
    layers = set(summary.detected_layers)
    reasons_front: list[str] = []
    reasons_back: list[str] = []
    if LAYER_FRONTEND in layers:
        reasons_front.append("a frontend layer was detected")
    if report.total_calls:
        reasons_front.append(f"{report.total_calls} frontend API call(s) were detected")
    if LAYER_BACKEND in layers or LAYER_API in layers:
        reasons_back.append("a backend/api layer was detected")
    if report.total_endpoints:
        reasons_back.append(f"{report.total_endpoints} backend endpoint(s) were detected")

    if reasons_front and reasons_back:
        return PROJECT_FULL_STACK, "; ".join(reasons_front + reasons_back)
    if reasons_front:
        return PROJECT_FRONTEND_ONLY, "; ".join(reasons_front) + "; no backend evidence was found"
    if reasons_back:
        return PROJECT_BACKEND_ONLY, "; ".join(reasons_back) + "; no frontend evidence was found"
    return (
        PROJECT_UNDETERMINED,
        "no frontend or backend layer, API call or API endpoint could be identified",
    )


def _build_uncertainties(
    summary: ArchitectureSummary,
    report: ApiMappingReport,
    parse_error_files: list[str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    # 1. API findings the matcher itself could not decide.
    for result in report.uncertain:
        data = _result_dict(result)
        items.append(
            {
                "kind": "api",
                "message": result.reason,
                "confidence": result.confidence,
                "evidence": data["evidence"],
            }
        )

    # 2. Layers whose classification evidence is weak.
    for layer in LAYER_ORDER:
        item = summary.layers.get(layer)
        if item is None or layer == LAYER_UNKNOWN:
            continue
        if item.confidence == CONFIDENCE_LOW:
            items.append(
                {
                    "kind": "layer",
                    "message": (
                        f"The '{layer}' layer ({item.files} file(s)) was inferred from weak "
                        "evidence (file names or ambiguous directory names)."
                    ),
                    "confidence": CONFIDENCE_LOW,
                    "evidence": [],
                }
            )

    # 3. Files with no layer evidence at all.
    unknown = summary.layers.get(LAYER_UNKNOWN)
    if unknown is not None:
        items.append(
            {
                "kind": "layer",
                "message": (
                    f"{unknown.files} file(s) could not be assigned to any layer "
                    "(no conventional directory or file-name evidence)."
                ),
                "confidence": CONFIDENCE_LOW,
                "evidence": [],
            }
        )

    # 4. Endpoint/call sites the static detectors had to skip.
    endpoint_summary = report.endpoint_summary
    call_summary = report.call_summary
    if endpoint_summary is not None and endpoint_summary.dynamic_paths_skipped:
        items.append(
            {
                "kind": "coverage",
                "message": (
                    f"{endpoint_summary.dynamic_paths_skipped} backend route declaration(s) use "
                    "a non-literal path or method and could not be read statically."
                ),
                "confidence": CONFIDENCE_LOW,
                "evidence": [],
            }
        )
    if call_summary is not None and call_summary.dynamic_urls_skipped:
        items.append(
            {
                "kind": "coverage",
                "message": (
                    f"{call_summary.dynamic_urls_skipped} frontend call-like expression(s) build "
                    "their URL or method dynamically and could not be read statically."
                ),
                "confidence": CONFIDENCE_LOW,
                "evidence": [],
            }
        )

    # 5. Files that failed to parse are invisible to the API analyzers.
    if parse_error_files:
        items.append(
            {
                "kind": "coverage",
                "message": (
                    f"{len(parse_error_files)} file(s) could not be parsed and were not searched "
                    "for API calls or endpoints."
                ),
                "confidence": CONFIDENCE_LOW,
                "evidence": [{"side": "file", "file": path, "line": None, "detail": "parse error"} for path in parse_error_files],
            }
        )
    return items


def _build_diagram(
    summary: ArchitectureSummary, report: ApiMappingReport
) -> dict[str, Any]:
    """
    Project the real results onto a node/edge graph.

    Nodes: every layer that received files (``unknown`` included), carrying its
    real file/LOC/language numbers and the count of API findings located in it.
    Edges: one per ordered (call layer -> endpoint layer) pair that has matched
    calls. A match whose two sides sit in the same layer is not an inter-layer
    edge; it is counted on the node as ``internal_matches``.
    """
    unmatched_by_layer: dict[str, int] = defaultdict(int)
    unused_by_layer: dict[str, int] = defaultdict(int)
    uncertain_by_layer: dict[str, int] = defaultdict(int)
    for result in report.unmatched_frontend_calls:
        unmatched_by_layer[(result.call.layer or LAYER_UNKNOWN)] += 1
    for result in report.unused_backend_endpoints:
        unused_by_layer[(result.endpoint.layer or LAYER_UNKNOWN)] += 1
    for result in report.uncertain:
        side = result.call or result.endpoint
        uncertain_by_layer[(side.layer or LAYER_UNKNOWN)] += 1

    edge_data: dict[tuple[str, str], dict[str, Any]] = {}
    internal: dict[str, int] = defaultdict(int)
    for result in report.matched:
        source = result.call.layer or LAYER_UNKNOWN
        target = result.endpoint.layer or LAYER_UNKNOWN
        if source == target:
            internal[source] += 1
            continue
        entry = edge_data.setdefault(
            (source, target), {"count": 0, "confidences": []}
        )
        entry["count"] += 1
        entry["confidences"].append(result.confidence)

    nodes: list[dict[str, Any]] = []
    for layer in LAYER_ORDER:
        item = summary.layers.get(layer)
        if item is None:
            continue
        nodes.append(
            {
                "id": layer,
                "layer": layer,
                "label": layer,
                "files": item.files,
                "loc": item.loc,
                "languages": dict(item.languages),
                "confidence": item.confidence,
                "unmatched_calls": unmatched_by_layer.get(layer, 0),
                "unused_endpoints": unused_by_layer.get(layer, 0),
                "uncertain": uncertain_by_layer.get(layer, 0),
                "internal_matches": internal.get(layer, 0),
            }
        )

    edges = [
        {
            "source": source,
            "target": target,
            "kind": "api",
            "label": f"{data['count']} matched API call{'s' if data['count'] != 1 else ''}",
            "count": data["count"],
            "confidence": _min_confidence(data["confidences"]),
        }
        for (source, target), data in sorted(edge_data.items())
    ]
    return {"nodes": nodes, "edges": edges, "note": DIAGRAM_NOTE}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def build_architecture_report(
    modules: Iterable[Any],
    parsed_modules: Iterable[ParsedModule],
) -> dict[str, Any]:
    """
    Run the Phase 8A–8C analyzers over one analyzed project and assemble the
    persisted/served report.

    `modules` are the project's analyzed files (``ProjectAnalysis.modules``);
    `parsed_modules` the matching parse results
    (``analyze_project(root, retain_parsed=True).parsed_modules``) — the single
    parse the metric pipeline already did, never a second scan.
    """
    module_list = list(modules)
    parsed_list = list(parsed_modules)

    summary = analyze_architecture(module_list)
    mapping = analyze_api_mapping(parsed_list, summary)

    project_type, basis = _project_type(summary, mapping)
    parse_error_files = sorted(
        str(getattr(m, "file_path", ""))
        for m in module_list
        if getattr(m, "parse_error", None)
    )

    endpoint_summary = mapping.endpoint_summary
    call_summary = mapping.call_summary
    endpoints = [e.to_dict() for e in endpoint_summary.endpoints] if endpoint_summary else []
    calls = [c.to_dict() for c in call_summary.calls] if call_summary else []

    return {
        "schema_version": SCHEMA_VERSION,
        "project_type": project_type,
        "project_type_basis": basis,
        "confidence": summary.confidence,
        "summary": {
            "total_files": summary.total_files,
            "total_loc": summary.total_loc,
            "detected_layers": list(summary.detected_layers),
            "is_multi_layer": summary.is_multi_layer,
            "languages": dict(summary.languages),
        },
        "layers": _layer_dict(summary),
        "files": [item.to_dict() for item in summary.classifications],
        "endpoints": endpoints,
        "calls": calls,
        "api_mapping": {
            **mapping.to_dict(),
            "matched_items": [_result_dict(r) for r in mapping.matched],
            "unmatched_frontend_calls_items": [_result_dict(r) for r in mapping.unmatched_frontend_calls],
            "unused_backend_endpoints_items": [_result_dict(r) for r in mapping.unused_backend_endpoints],
            "uncertain_items": [_result_dict(r) for r in mapping.uncertain],
        },
        "uncertainties": _build_uncertainties(summary, mapping, parse_error_files),
        "diagram": _build_diagram(summary, mapping),
        "coverage": {
            "endpoints": endpoint_summary.to_dict() if endpoint_summary else {},
            "calls": call_summary.to_dict() if call_summary else {},
            "files_with_parse_errors": len(parse_error_files),
        },
        "note": API_MATCHING_NOTE,
    }


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    """Compact headline numbers (used in the upload response and DB columns)."""
    mapping = report["api_mapping"]
    return {
        "project_type": report["project_type"],
        "confidence": report["confidence"],
        "detected_layers": list(report["summary"]["detected_layers"]),
        "total_endpoints": mapping["total_endpoints"],
        "total_calls": mapping["total_calls"],
        "matched": mapping["matched"],
        "unmatched_frontend_calls": mapping["unmatched_frontend_calls"],
        "unused_backend_endpoints": mapping["unused_backend_endpoints"],
        "uncertain": len(report["uncertainties"]),
    }
