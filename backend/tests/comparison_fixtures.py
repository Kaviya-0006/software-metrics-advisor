"""
Controlled V1 -> V2 fixtures for the Phase 9B comparison engine.

Every difference between the two versions is deliberate and listed in
EXPECTED, so tests assert exact known outcomes instead of "something changed".
The module dicts have the same shape as stored `modules` rows.
"""

from __future__ import annotations

from typing import Any


def mod(path: str, **over: Any) -> dict[str, Any]:
    base = {
        "file_path": path, "language": "python",
        "loc_total": 50, "loc_code": 40, "loc_comments": 6, "loc_blank": 4,
        "classes": 1, "functions": 4,
        "complexity_total": 10, "complexity_average": 2.5, "complexity_maximum": 4,
        "maintainability_index": 70.0, "coupling": 2, "cohesion": 1.0,
        "depth_of_inheritance": 0.0, "duplication_percentage": 0.0, "duplicated_lines": 0,
        "risk_score": 20.0, "risk_level": "LOW", "parse_error": None,
    }
    base.update(over)
    return base


def version_row(number: int, scan_id: int, **over: Any) -> dict[str, Any]:
    row = {
        "scan_id": scan_id, "version_number": number, "scanned_at": f"2026-10-0{number}T10:00:00Z",
        "health_score": 70.0, "health_status": "GOOD",
        "category_scores": {"complexity": 60.0, "documentation": 40.0, "testing": None},
        "metrics": {
            "average_complexity": 3.0, "total_complexity": 60, "average_maintainability": 70.0,
            "duplication_percentage": 4.0, "duplicated_lines": 20, "average_risk_score": 30.0,
            "max_risk_score": 50.0, "high_risk_modules": 0,
            "risk_summary": {"low": 6, "medium": 2, "high": 0, "critical": 0},
            "total_loc": 400, "files_analyzed": 8, "total_classes": 8, "total_functions": 32,
        },
        "languages": {"python": {"files": 8, "loc": 400}},
        "is_backfilled": False,
    }
    for key, value in over.items():
        if key == "metrics":
            row["metrics"] = {**row["metrics"], **value}
        else:
            row[key] = value
    return row


def v1_modules(root: str = "") -> list[dict[str, Any]]:
    prefix = f"{root}/" if root else ""
    return [
        mod(f"{prefix}app/stable.py"),                                                    # unchanged (hash equal)
        mod(f"{prefix}app/hot.py", complexity_total=10, complexity_maximum=4),            # complexity regression in V2
        mod(f"{prefix}app/clean.py", maintainability_index=60.0, risk_score=45.0, risk_level="MEDIUM"),  # improves
        mod(f"{prefix}app/tight.py", coupling=2),                                         # coupling regression
        mod(f"{prefix}app/risky.py", risk_score=55.0, risk_level="MEDIUM"),               # MEDIUM -> HIGH
        mod(f"{prefix}app/old_name.py", loc_total=80, complexity_total=12),               # renamed, same content
        mod(f"{prefix}app/deleted.py", risk_score=85.0, risk_level="CRITICAL"),           # removed, no rename
        mod(f"{prefix}app/broken.py", parse_error="SyntaxError", complexity_total=None, maintainability_index=None),
        mod(f"{prefix}web/app.ts", language="typescript"),                                # unchanged by metrics, no hash
    ]


def v2_modules(root: str = "") -> list[dict[str, Any]]:
    prefix = f"{root}/" if root else ""
    return [
        mod(f"{prefix}app/stable.py"),
        mod(f"{prefix}app/hot.py", complexity_total=30, complexity_maximum=12, risk_score=48.0, risk_level="MEDIUM"),
        mod(f"{prefix}app/clean.py", maintainability_index=75.0, risk_score=25.0, risk_level="LOW"),
        mod(f"{prefix}app/tight.py", coupling=6),
        mod(f"{prefix}app/risky.py", risk_score=65.0, risk_level="HIGH"),
        mod(f"{prefix}app/new_name.py", loc_total=80, complexity_total=12),
        mod(f"{prefix}app/brand_new.py", risk_score=90.0, risk_level="CRITICAL", language="python"),  # added, critical
        mod(f"{prefix}app/broken.py", parse_error="SyntaxError", complexity_total=None, maintainability_index=None),
        mod(f"{prefix}web/app.ts", language="typescript"),
    ]


def hashes(version: int, root: str = "") -> dict[str, str]:
    """Content hashes: stable & new/old name share content; hot/clean/etc differ; app.ts has none."""
    prefix = f"{root}/" if root else ""
    common = {
        "app/stable.py": "h-stable",
        "app/old_name.py": "h-moved", "app/new_name.py": "h-moved",
        "app/broken.py": "h-broken",
        "app/hot.py": f"h-hot-{version}", "app/clean.py": f"h-clean-{version}",
        "app/tight.py": f"h-tight-{version}", "app/risky.py": f"h-risky-{version}",
        "app/deleted.py": "h-deleted", "app/brand_new.py": "h-brand-new",
    }
    wanted = {
        1: ["app/stable.py", "app/old_name.py", "app/broken.py", "app/hot.py", "app/clean.py", "app/tight.py", "app/risky.py", "app/deleted.py"],
        2: ["app/stable.py", "app/new_name.py", "app/broken.py", "app/hot.py", "app/clean.py", "app/tight.py", "app/risky.py", "app/brand_new.py"],
    }[version]
    return {f"{prefix}{p}": common[p] for p in wanted}


def arch_report(extra_endpoint: bool, broken_call: bool) -> dict[str, Any]:
    def ev(side, file, line, detail):
        return {"side": side, "file": file, "line": line, "detail": detail}

    endpoints = [{"method": "GET", "path": "/api/users", "source_file": "backend/main.py", "line_number": 3}]
    calls = [{"method": "GET", "path": "/api/users", "source_file": "frontend/api.js", "line_number": 1}]
    matched = [{"frontend": {"call": "GET /api/users", "file": "frontend/api.js", "line": 1},
                "backend": {"endpoint": "GET /api/users", "file": "backend/main.py", "line": 3},
                "evidence": [ev("frontend", "frontend/api.js", 1, "GET /api/users"), ev("backend", "backend/main.py", 3, "GET /api/users")]}]
    unused, unmatched = [], []
    if extra_endpoint:
        endpoints.append({"method": "DELETE", "path": "/api/users/{id}", "source_file": "backend/main.py", "line_number": 9})
        unused.append({"backend": {"endpoint": "DELETE /api/users/{id}", "file": "backend/main.py", "line": 9},
                       "evidence": [ev("backend", "backend/main.py", 9, "DELETE /api/users/{id}")]})
    if broken_call:
        calls.append({"method": "POST", "path": "/api/missing", "source_file": "frontend/api.js", "line_number": 2})
        unmatched.append({"frontend": {"call": "POST /api/missing", "file": "frontend/api.js", "line": 2},
                          "evidence": [ev("frontend", "frontend/api.js", 2, "POST /api/missing")]})
    return {
        "project_type": "full-stack",
        "layers": [{"layer": "frontend", "files": 1, "loc": 5}, {"layer": "backend", "files": 1 + int(extra_endpoint), "loc": 10}],
        "endpoints": endpoints, "calls": calls,
        "api_mapping": {
            "matched_items": matched, "unmatched_frontend_calls_items": unmatched,
            "unused_backend_endpoints_items": unused, "uncertain": 0,
        },
    }


def before_after(root_before: str = "", root_after: str = "", with_hashes: bool = True, with_arch: bool = True):
    before = {
        "version": version_row(1, 11),
        "modules": v1_modules(root_before),
        "fingerprints": hashes(1, root_before) if with_hashes else {},
        "architecture": arch_report(False, False) if with_arch else None,
    }
    after = {
        "version": version_row(
            2, 12, health_score=60.0, category_scores={"complexity": 50.0, "documentation": 30.0, "testing": None},
            metrics={
                "average_complexity": 4.5, "average_maintainability": 64.0, "duplication_percentage": 7.0,
                "average_risk_score": 41.0, "high_risk_modules": 3,
                "risk_summary": {"low": 4, "medium": 1, "high": 2, "critical": 1},
                "total_loc": 460, "files_analyzed": 9,
            },
            languages={"python": {"files": 8, "loc": 440}, "java": {"files": 1, "loc": 20}},
        ),
        "modules": v2_modules(root_after),
        "fingerprints": hashes(2, root_after) if with_hashes else {},
        "architecture": arch_report(True, True) if with_arch else None,
    }
    return before, after


# Known, deliberate differences (what the tests assert).
EXPECTED = {
    "status": {
        "app/stable.py": "unchanged",
        "app/hot.py": "changed",
        "app/clean.py": "changed",
        "app/tight.py": "changed",
        "app/risky.py": "changed",
        "app/broken.py": "unchanged",
        "web/app.ts": "unverified",
        "app/deleted.py": "removed",
        "app/old_name.py": "removed",
        "app/new_name.py": "added",
        "app/brand_new.py": "added",
    },
}
