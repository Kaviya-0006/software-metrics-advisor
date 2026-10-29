"""
Version comparison and file-level regression intelligence (Phase 9B).

Pure functions over data that earlier phases already stored: the per-version
snapshot (Phase 9A), the per-file module rows (Phase 2/3), the per-file content
fingerprints (Phase 9B) and the saved architecture report (Phase 8D). Nothing
here re-analyzes source, recomputes any metric with a second implementation, or
touches the database - so every number in a comparison is a stored value or the
difference of two stored values.

Principles
----------
* **Stored values only.** A metric that a version never recorded is ``None``
  and is reported as unavailable - never as 0, never interpolated.
* **Safe arithmetic.** The percentage change is ``None`` (with a reason) when
  the previous value is missing or zero; no division by zero is ever attempted.
* **Deterministic, transparent rules.** Whether a change is a significant
  regression/improvement is decided by the threshold tables ``PROJECT_RULES``
  and ``FILE_RULES`` below (also returned in every response). Same input, same
  output, no heuristics hidden in code paths.
* **ZIP root renames are neutral.** Paths are normalized, and a single shared
  top-level folder (``proj-v1/``) is ignored on whichever side has one when -
  and only when - that yields the best path overlap between the two versions.
* **Honest about uncertainty.** "Unchanged" means the stored content hash is
  equal (verified); with no hash available the file is ``unverified`` (no
  tracked metric changed, content not provable). A removed + added pair that
  looks like a move/rename is *reported as a possible rename with a confidence
  and the evidence*, never silently merged or silently split.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------
# Transparent rule tables
# --------------------------------------------------------------------------

# Project-level: a change is *significant* when |absolute change| >= min_abs
# and (when set) |percent change| >= min_pct (a previous value of 0 has no
# percentage, so then only min_abs applies). "severity: high" at >= 2x.
PROJECT_RULES: dict[str, dict[str, Any]] = {
    "health_score": {"label": "Project health score", "higher_is_better": True, "min_abs": 5.0},
    "average_complexity": {"label": "Average complexity", "higher_is_better": False, "min_abs": 0.5, "min_pct": 10.0},
    "average_maintainability": {"label": "Average maintainability", "higher_is_better": True, "min_abs": 5.0},
    "duplication_percentage": {"label": "Duplication", "higher_is_better": False, "min_abs": 2.0},
    "documentation_score": {"label": "Documentation score", "higher_is_better": True, "min_abs": 5.0},
    "average_risk_score": {"label": "Average risk score", "higher_is_better": False, "min_abs": 5.0},
    "high_risk_modules": {"label": "High + critical risk modules", "higher_is_better": False, "min_abs": 1.0},
}

# File-level (same file path in both versions). A rule fires when the metric
# is stored in BOTH versions and |change| >= min_abs and (when set) the
# relative change >= min_pct. Metrics named here are "lower is better" unless
# higher_is_better is true.
FILE_RULES: list[dict[str, Any]] = [
    {"id": "complexity_total", "metric": "complexity_total", "label": "Total complexity", "higher_is_better": False, "min_abs": 5, "min_pct": 20.0},
    {"id": "complexity_maximum", "metric": "complexity_maximum", "label": "Most complex function", "higher_is_better": False, "min_abs": 5},
    {"id": "maintainability_index", "metric": "maintainability_index", "label": "Maintainability index", "higher_is_better": True, "min_abs": 5.0},
    {"id": "coupling", "metric": "coupling", "label": "Coupling", "higher_is_better": False, "min_abs": 3},
    {"id": "cohesion", "metric": "cohesion", "label": "Cohesion (LCOM1, lower is better)", "higher_is_better": False, "min_abs": 3.0},
    {"id": "duplication_percentage", "metric": "duplication_percentage", "label": "Duplication", "higher_is_better": False, "min_abs": 10.0},
    {"id": "risk_score", "metric": "risk_score", "label": "Risk score", "higher_is_better": False, "min_abs": 10.0},
]

# A move of a file's risk level between these bands is itself a signal.
RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
HIGH_RISK_LEVELS = ("HIGH", "CRITICAL")

# Metrics shown for every changed file (value change only; verdicts come from FILE_RULES).
FILE_METRICS: list[tuple[str, bool | None]] = [
    ("loc_total", None),
    ("complexity_total", False),
    ("complexity_maximum", False),
    ("maintainability_index", True),
    ("coupling", False),
    ("cohesion", False),
    ("duplication_percentage", False),
    ("risk_score", False),
]

# Stored per-file fields that together describe a file; equal on both sides
# means "no tracked metric changed".
SIGNATURE_FIELDS = [
    "loc_total", "loc_code", "loc_comments", "loc_blank", "classes", "functions",
    "complexity_total", "complexity_average", "complexity_maximum", "maintainability_index",
    "coupling", "cohesion", "depth_of_inheritance", "duplication_percentage",
    "duplicated_lines", "risk_score", "risk_level",
]

_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
_EPS = 1e-9


# --------------------------------------------------------------------------
# Safe metric arithmetic
# --------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def metric_change(previous: Any, current: Any, higher_is_better: bool | None = None) -> dict[str, Any]:
    """
    Previous / current / absolute / percent change of one stored metric.

    ``direction`` is ``unavailable`` when either side is missing, ``unchanged``
    when equal, otherwise ``improved`` / ``regressed`` (judged by
    ``higher_is_better``) or ``changed`` when the metric has no good direction
    (LOC, file counts). ``percent_change`` is ``None`` when the previous value
    is missing or zero, with the reason in ``percent_note`` - never a division
    by zero and never an invented number.
    """
    prev, cur = _num(previous), _num(current)
    result: dict[str, Any] = {
        "previous": _round(prev, 4),
        "current": _round(cur, 4),
        "absolute_change": None,
        "percent_change": None,
        "percent_note": None,
        "direction": "unavailable",
    }
    if prev is None or cur is None:
        missing = "previous" if prev is None else "current"
        if prev is None and cur is None:
            missing = "both"
        result["percent_note"] = f"not available ({missing} value not recorded)"
        return result

    delta = cur - prev
    result["absolute_change"] = _round(delta, 4)
    if abs(delta) < _EPS:
        result["absolute_change"] = 0.0
        result["percent_change"] = 0.0 if prev != 0 else None
        if prev == 0:
            result["percent_note"] = "previous value is 0"
        result["direction"] = "unchanged"
        return result

    if prev == 0:
        result["percent_note"] = "undefined (previous value is 0)"
    else:
        result["percent_change"] = _round(delta / abs(prev) * 100.0, 2)

    if higher_is_better is None:
        result["direction"] = "changed"
    else:
        result["direction"] = "improved" if (delta > 0) == higher_is_better else "regressed"
    return result


def _is_significant(change: dict[str, Any], min_abs: float | None, min_pct: float | None) -> bool:
    delta = change["absolute_change"]
    if delta is None or abs(delta) < _EPS:
        return False
    if min_abs is not None and abs(delta) + _EPS < min_abs:
        return False
    pct = change["percent_change"]
    if min_pct is not None and pct is not None and abs(pct) + _EPS < min_pct:
        return False
    return True


def _severity(change: dict[str, Any], min_abs: float | None, min_pct: float | None) -> str:
    delta = abs(change["absolute_change"] or 0.0)
    pct = abs(change["percent_change"]) if change["percent_change"] is not None else None
    if min_abs is not None and delta + _EPS >= 2 * min_abs:
        return "high"
    if min_pct is not None and pct is not None and pct + _EPS >= 2 * min_pct:
        return "high"
    return "medium"


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def normalize_path(path: str) -> str:
    """Forward slashes, no leading './' or '/', no empty segments."""
    cleaned = str(path or "").replace("\\", "/")
    parts = [p for p in cleaned.split("/") if p and p != "."]
    return "/".join(parts)


def _shared_root(paths: Iterable[str]) -> str | None:
    """The single top-level folder every path lives in, else None."""
    paths = list(paths)
    if not paths or any("/" not in p for p in paths):
        return None
    first = paths[0].split("/", 1)[0]
    return first if all(p.split("/", 1)[0] == first for p in paths) else None


def _keyed(modules: list[Mapping[str, Any]], strip: str | None) -> dict[str, Mapping[str, Any]]:
    keyed: dict[str, Mapping[str, Any]] = {}
    for module in modules:
        path = normalize_path(module["file_path"])
        if strip and path.startswith(strip + "/"):
            path = path[len(strip) + 1 :]
        keyed[path] = module
    return keyed


def align_paths(
    before: list[Mapping[str, Any]], after: list[Mapping[str, Any]]
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]], dict[str, Any]]:
    """
    Key both versions' files by normalized relative path, ignoring a shared
    ZIP root folder on a side when that gives the best overlap. Returns
    (before_by_path, after_by_path, alignment_info).
    """
    root_before = _shared_root(normalize_path(m["file_path"]) for m in before)
    root_after = _shared_root(normalize_path(m["file_path"]) for m in after)
    options: list[tuple[str | None, str | None]] = [(None, None)]
    if root_before or root_after:
        options += [(root_before, root_after), (root_before, None), (None, root_after)]

    best = None
    for strip_before, strip_after in options:
        keyed_before = _keyed(before, strip_before)
        keyed_after = _keyed(after, strip_after)
        overlap = len(keyed_before.keys() & keyed_after.keys())
        if best is None or overlap > best[0]:
            best = (overlap, strip_before, strip_after, keyed_before, keyed_after)
    assert best is not None
    _, strip_before, strip_after, keyed_before, keyed_after = best

    notes = []
    if strip_before or strip_after:
        notes.append(
            "A shared ZIP root folder was ignored when matching files "
            f"(before: {strip_before or 'none'}, after: {strip_after or 'none'}), "
            "so files are compared by their path inside the project."
        )
    info = {
        "previous_root_ignored": strip_before,
        "current_root_ignored": strip_after,
        "root_changed": bool(strip_before or strip_after) and (strip_before != strip_after),
        "matched_paths": len(keyed_before.keys() & keyed_after.keys()),
        "notes": notes,
    }
    return keyed_before, keyed_after, info


# --------------------------------------------------------------------------
# File comparison
# --------------------------------------------------------------------------


def _signature_equal(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    for field in SIGNATURE_FIELDS:
        va, vb = a.get(field), b.get(field)
        na, nb = _num(va), _num(vb)
        if na is not None or nb is not None:
            if na is None or nb is None or abs(na - nb) > _EPS:
                return False
        elif va != vb:
            return False
    return bool(a.get("parse_error")) == bool(b.get("parse_error"))


def _level_index(level: str | None) -> int | None:
    return RISK_LEVELS.index(level) if level in RISK_LEVELS else None


def _signal(rule: str, direction: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"rule": rule, "direction": direction, "message": message, **extra}


def _file_signals(prev: Mapping[str, Any], cur: Mapping[str, Any], changes: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Apply FILE_RULES + the risk-band rule. Returns (signals, missing_metrics)."""
    signals: list[dict] = []
    missing: list[str] = []
    for rule in FILE_RULES:
        change = changes.get(rule["metric"])
        if change is None:
            change = metric_change(prev.get(rule["metric"]), cur.get(rule["metric"]), rule["higher_is_better"])
        if change["direction"] == "unavailable":
            missing.append(rule["metric"])
            continue
        if not _is_significant(change, rule.get("min_abs"), rule.get("min_pct")):
            continue
        regressed = change["direction"] == "regressed"
        verb = "rose" if (change["absolute_change"] or 0) > 0 else "fell"
        pct = f" ({change['percent_change']:+.1f}%)" if change["percent_change"] is not None else ""
        signals.append(
            _signal(
                rule["id"],
                "regression" if regressed else "improvement",
                f"{rule['label']} {verb} from {change['previous']:g} to {change['current']:g}{pct}",
                metric=rule["metric"],
                previous=change["previous"],
                current=change["current"],
                absolute_change=change["absolute_change"],
                percent_change=change["percent_change"],
            )
        )

    prev_level, cur_level = prev.get("risk_level"), cur.get("risk_level")
    pi, ci = _level_index(prev_level), _level_index(cur_level)
    if pi is not None and ci is not None and pi != ci:
        worse = ci > pi
        signals.append(
            _signal(
                "risk_level",
                "regression" if worse else "improvement",
                f"Risk level {'rose' if worse else 'fell'} from {prev_level} to {cur_level}",
                metric="risk_level",
                previous=prev_level,
                current=cur_level,
            )
        )
    elif pi is None or ci is None:
        if "risk_level" not in missing:
            missing.append("risk_level")
    return signals, missing


def _row_common(path: str, module: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": path,
        "language": module.get("language"),
        "risk_level": module.get("risk_level"),
        "risk_score": _round(_num(module.get("risk_score")), 2),
        "loc_total": module.get("loc_total"),
    }


def _hash_of(module: Mapping[str, Any], hashes: Mapping[str, str]) -> str | None:
    return hashes.get(module["file_path"])


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _structure_key(module: Mapping[str, Any]) -> tuple:
    return tuple(module.get(f) for f in ("language", "loc_total", "loc_code", "classes", "functions", "complexity_total", "complexity_maximum"))


def _detect_renames(
    removed: dict[str, Mapping[str, Any]],
    added: dict[str, Mapping[str, Any]],
    hashes_before: Mapping[str, str],
    hashes_after: Mapping[str, str],
) -> list[dict[str, Any]]:
    """
    Possible renames among removed/added files, one-to-one, deterministic.

    high   - identical content hash (the content moved; only the path changed)
    medium - same file name and language and identical size/structure metrics
             (content hash unavailable)
    low    - same file name and language and a similar size (LOC within 25%);
             content differs or cannot be compared - may be a moved *and* edited file
    """
    candidates: list[tuple[int, str, str, str, str]] = []
    for rpath, rmod in removed.items():
        rhash = _hash_of(rmod, hashes_before)
        for apath, amod in added.items():
            ahash = _hash_of(amod, hashes_after)
            if rhash and ahash and rhash == ahash:
                candidates.append((3, rpath, apath, "high", "identical content hash"))
                continue
            if _basename(rpath) != _basename(apath) or rmod.get("language") != amod.get("language"):
                continue
            if rhash and ahash:
                reason_hash = "content hash differs"
            else:
                reason_hash = "content hash unavailable"
            if _structure_key(rmod) == _structure_key(amod):
                candidates.append((2, rpath, apath, "medium", f"same file name, language and size/structure metrics; {reason_hash}"))
                continue
            rl, al = _num(rmod.get("loc_total")), _num(amod.get("loc_total"))
            if rl is not None and al is not None and max(rl, al) > 0 and abs(rl - al) / max(rl, al) <= 0.25:
                candidates.append((1, rpath, apath, "low", f"same file name and language, similar size; {reason_hash}"))

    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_removed: set[str] = set()
    used_added: set[str] = set()
    renames = []
    for _, rpath, apath, confidence, reason in candidates:
        if rpath in used_removed or apath in used_added:
            continue
        used_removed.add(rpath)
        used_added.add(apath)
        renames.append({"from": rpath, "to": apath, "confidence": confidence, "reason": reason})
    return renames


def compare_files(
    before: list[Mapping[str, Any]],
    after: list[Mapping[str, Any]],
    hashes_before: Mapping[str, str],
    hashes_after: Mapping[str, str],
) -> dict[str, Any]:
    """Classify every file and compute per-file metric changes and verdicts."""
    keyed_before, keyed_after, alignment = align_paths(before, after)

    rows: list[dict[str, Any]] = []
    alerts_regress: list[dict[str, Any]] = []
    alerts_improve: list[dict[str, Any]] = []

    removed = {p: m for p, m in keyed_before.items() if p not in keyed_after}
    added = {p: m for p, m in keyed_after.items() if p not in keyed_before}
    renames = _detect_renames(removed, added, hashes_before, hashes_after)
    rename_from = {r["from"]: r for r in renames}
    rename_to = {r["to"]: r for r in renames}

    summary = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0, "unverified": 0}
    verdicts = {"regressed": 0, "improved": 0, "mixed": 0, "neutral": 0}
    notes_unavailable = 0

    for path in sorted(keyed_before.keys() | keyed_after.keys()):
        prev, cur = keyed_before.get(path), keyed_after.get(path)

        if prev is not None and cur is not None:
            hp, hc = _hash_of(prev, hashes_before), _hash_of(cur, hashes_after)
            parse_issue = bool(prev.get("parse_error") or cur.get("parse_error"))
            same_sig = _signature_equal(prev, cur)

            if hp and hc and hp == hc:
                status, verification = "unchanged", "content-hash"
            elif hp and hc:
                status, verification = "changed", "content-hash"
            elif same_sig:
                status, verification = "unverified", "metrics-only"
            else:
                status, verification = "changed", "metrics-only"
            summary[status] += 1

            row = {
                **_row_common(path, cur),
                "status": status,
                "verification": verification,
                "previous_path": prev["file_path"],
                "current_path": cur["file_path"],
                "previous_risk_level": prev.get("risk_level"),
                "current_risk_level": cur.get("risk_level"),
                "changes": {},
                "signals": [],
                "verdict": None,
                "notes": [],
            }
            if status == "unverified":
                row["notes"].append(
                    "No tracked metric changed, but this file's content cannot be verified as identical "
                    "(no stored content hash for one or both versions)."
                )
            if status == "changed":
                changes = {m: metric_change(prev.get(m), cur.get(m), better) for m, better in FILE_METRICS}
                row["changes"] = changes
                if parse_issue:
                    row["notes"].append(
                        "A parse error affected this file in at least one version, so its metrics are not "
                        "comparable and no regression rules were applied."
                    )
                    row["verdict"] = "neutral"
                    notes_unavailable += 1
                else:
                    signals, missing = _file_signals(prev, cur, changes)
                    row["signals"] = signals
                    if missing:
                        row["notes"].append("Not recorded in one or both versions: " + ", ".join(sorted(missing)) + ".")
                        notes_unavailable += 1
                    reg = [s for s in signals if s["direction"] == "regression"]
                    imp = [s for s in signals if s["direction"] == "improvement"]
                    if reg and imp:
                        row["verdict"] = "mixed"
                    elif reg:
                        row["verdict"] = "regressed"
                    elif imp:
                        row["verdict"] = "improved"
                    else:
                        row["verdict"] = "neutral"
                    if hp and hc and not signals and all(
                        c["direction"] in ("unchanged", "unavailable") for c in changes.values()
                    ):
                        row["notes"].append("Content changed but no tracked metric changed.")
                    verdicts[row["verdict"]] += 1

                    if reg:
                        reached_high = cur.get("risk_level") in HIGH_RISK_LEVELS
                        severity = "high" if (len(reg) >= 2 or reached_high) else "medium"
                        alerts_regress.append(
                            {
                                "scope": "file",
                                "id": f"file:{path}",
                                "title": f"{path} regressed",
                                "detail": "; ".join(s["message"] for s in reg),
                                "severity": severity,
                                "path": path,
                                "language": cur.get("language"),
                                "rules": [s["rule"] for s in reg],
                            }
                        )
                    if imp:
                        alerts_improve.append(
                            {
                                "scope": "file",
                                "id": f"file:{path}",
                                "title": f"{path} improved",
                                "detail": "; ".join(s["message"] for s in imp),
                                "severity": "high" if len(imp) >= 2 else "medium",
                                "path": path,
                                "language": cur.get("language"),
                                "rules": [s["rule"] for s in imp],
                            }
                        )
            rows.append(row)
            continue

        if cur is not None:  # added
            summary["added"] += 1
            row = {
                **_row_common(path, cur),
                "status": "added",
                "verification": None,
                "previous_path": None,
                "current_path": cur["file_path"],
                "previous_risk_level": None,
                "current_risk_level": cur.get("risk_level"),
                "changes": {},
                "signals": [],
                "verdict": None,
                "notes": [],
            }
            rename = rename_to.get(path)
            if rename:
                row["possible_rename_of"] = {"path": rename["from"], "confidence": rename["confidence"], "reason": rename["reason"]}
                row["notes"].append(
                    f"Possible rename of {rename['from']} ({rename['confidence']} confidence: {rename['reason']}). "
                    "Counted as an added file; this is not a confirmed rename."
                )
            if cur.get("risk_level") in HIGH_RISK_LEVELS:
                row["signals"].append(_signal("new_high_risk_file", "regression", f"New file starts at {cur['risk_level']} risk"))
                alerts_regress.append(
                    {
                        "scope": "file",
                        "id": f"added:{path}",
                        "title": f"New high-risk file {path}",
                        "detail": f"Added with risk level {cur['risk_level']}"
                        + (" (possibly a renamed file - see rename note)" if rename else ""),
                        "severity": "high" if cur.get("risk_level") == "CRITICAL" else "medium",
                        "path": path,
                        "language": cur.get("language"),
                        "rules": ["new_high_risk_file"],
                    }
                )
            rows.append(row)
            continue

        # removed
        summary["removed"] += 1
        row = {
            **_row_common(path, prev),
            "status": "removed",
            "verification": None,
            "previous_path": prev["file_path"],
            "current_path": None,
            "previous_risk_level": prev.get("risk_level"),
            "current_risk_level": None,
            "changes": {},
            "signals": [],
            "verdict": None,
            "notes": [],
        }
        rename = rename_from.get(path)
        if rename:
            row["possible_rename_to"] = {"path": rename["to"], "confidence": rename["confidence"], "reason": rename["reason"]}
            row["notes"].append(
                f"Possibly renamed to {rename['to']} ({rename['confidence']} confidence: {rename['reason']}). "
                "Counted as a removed file; this is not a confirmed rename."
            )
        elif prev.get("risk_level") in HIGH_RISK_LEVELS:
            row["signals"].append(_signal("high_risk_file_removed", "improvement", f"File removed ({prev['risk_level']} risk)"))
            alerts_improve.append(
                {
                    "scope": "file",
                    "id": f"removed:{path}",
                    "title": f"High-risk file removed: {path}",
                    "detail": f"Removed; it had risk level {prev['risk_level']} (no rename candidate found, but renames cannot be ruled out)",
                    "severity": "medium",
                    "path": path,
                    "language": prev.get("language"),
                    "rules": ["high_risk_file_removed"],
                }
            )
        rows.append(row)

    rows.sort(key=lambda r: (_status_order(r["status"]), r["path"]))
    summary.update(
        {
            "total_previous": len(keyed_before),
            "total_current": len(keyed_after),
            "verdicts": verdicts,
            "possible_renames": {c: sum(1 for r in renames if r["confidence"] == c) for c in ("high", "medium", "low")},
            "files_with_missing_data": notes_unavailable,
        }
    )
    return {
        "summary": summary,
        "items": rows,
        "possible_renames": renames,
        "path_alignment": alignment,
        "_regressions": alerts_regress,
        "_improvements": alerts_improve,
    }


def _status_order(status: str) -> int:
    return {"changed": 0, "added": 1, "removed": 2, "unverified": 3, "unchanged": 4}.get(status, 9)


def calculate_code_churn(
    before: list[Mapping[str, Any]] | None,
    after: list[Mapping[str, Any]],
    hashes_before: Mapping[str, str] | None = None,
    hashes_after: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """
    Calculate a deterministic version-to-version code-churn signal.

    This is intentionally based only on the stored file-level comparison; it
    does not execute source code or require Git history. Because exact line
    diffs are not stored, a changed file contributes its absolute LOC delta
    as the conservative measurable change, while added and removed files
    contribute their full LOC.

    The category score is higher when churn is lower:
        churn_rate = churned_loc / previous_loc * 100
        score = 100 - min(churn_rate, 100)

    A first version has no previous baseline, so its score is unavailable.
    """
    if not before:
        return {
            "available": False,
            "score": None,
            "reason": "Code Churn needs a previous version of the same project.",
            "added_files": 0,
            "modified_files": 0,
            "removed_files": 0,
            "added_loc": 0,
            "modified_loc": 0,
            "removed_loc": 0,
            "churned_loc": 0,
            "previous_loc": 0,
            "churn_rate_percent": None,
        }

    comparison = compare_files(
        before,
        after,
        hashes_before or {},
        hashes_after or {},
    )
    added_loc = 0
    modified_loc = 0
    removed_loc = 0
    added_files = modified_files = removed_files = 0

    for item in comparison["items"]:
        status = item["status"]
        if status == "added":
            added_files += 1
            added_loc += int(item.get("loc_total") or 0)
        elif status == "removed":
            removed_files += 1
            removed_loc += int(item.get("loc_total") or 0)
        elif status == "changed":
            modified_files += 1
            previous_loc = item.get("changes", {}).get("loc_total", {}).get("previous")
            current_loc = item.get("changes", {}).get("loc_total", {}).get("current")
            if previous_loc is not None and current_loc is not None:
                # Exact changed lines are unavailable without a source diff.
                # Use the measurable LOC delta so a normal modified file does
                # not count its entire previous file as churn.
                modified_loc += abs(int(current_loc) - int(previous_loc))
            elif item.get("loc_total") is not None:
                modified_loc += int(item.get("loc_total") or 0)

    previous_loc = sum(int(m.get("loc_total") or 0) for m in before)
    churned_loc = added_loc + modified_loc + removed_loc

    if previous_loc <= 0:
        return {
            "available": False,
            "score": None,
            "reason": "The previous version has no measurable LOC baseline.",
            "added_files": added_files,
            "modified_files": modified_files,
            "removed_files": removed_files,
            "added_loc": added_loc,
            "modified_loc": modified_loc,
            "removed_loc": removed_loc,
            "churned_loc": churned_loc,
            "previous_loc": previous_loc,
            "churn_rate_percent": None,
        }

    churn_rate = round(churned_loc / previous_loc * 100.0, 2)
    score = round(max(0.0, 100.0 - min(churn_rate, 100.0)), 2)
    return {
        "available": True,
        "score": score,
        "reason": None,
        "added_files": added_files,
        "modified_files": modified_files,
        "removed_files": removed_files,
        "added_loc": added_loc,
        "modified_loc": modified_loc,
        "removed_loc": removed_loc,
        "churned_loc": churned_loc,
        "previous_loc": previous_loc,
        "churn_rate_percent": churn_rate,
    }


# --------------------------------------------------------------------------
# Project-level comparison
# --------------------------------------------------------------------------


def _comment_ratio(modules: list[Mapping[str, Any]]) -> float | None:
    """Comment lines per 100 code lines over the stored module rows - the same
    ratio the documentation score is based on (None if it cannot be formed)."""
    comments = [_num(m.get("loc_comments")) for m in modules if not m.get("parse_error")]
    code = [_num(m.get("loc_code")) for m in modules if not m.get("parse_error")]
    if not comments or any(c is None for c in comments) or any(c is None for c in code):
        return None
    total_code = sum(code)  # type: ignore[arg-type]
    if total_code <= 0:
        return None
    return round(sum(comments) / total_code * 100.0, 2)  # type: ignore[arg-type]


def _risk_distribution(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    prev = (before.get("metrics") or {}).get("risk_summary")
    cur = (after.get("metrics") or {}).get("risk_summary")
    levels = {}
    for key in ("low", "medium", "high", "critical"):
        levels[key] = metric_change(
            None if prev is None else prev.get(key),
            None if cur is None else cur.get(key),
            True if key == "low" else False if key in ("high", "critical") else None,
        )
    return {"levels": levels, "available": prev is not None and cur is not None}


def _project_metrics(before: Mapping[str, Any], after: Mapping[str, Any], mods_before, mods_after) -> dict[str, Any]:
    mb, ma = before.get("metrics") or {}, after.get("metrics") or {}
    cb, ca = before.get("category_scores") or {}, after.get("category_scores") or {}
    pairs: dict[str, tuple[Any, Any, bool | None]] = {
        "health_score": (before.get("health_score"), after.get("health_score"), True),
        "average_complexity": (mb.get("average_complexity"), ma.get("average_complexity"), False),
        "average_maintainability": (mb.get("average_maintainability"), ma.get("average_maintainability"), True),
        "duplication_percentage": (mb.get("duplication_percentage"), ma.get("duplication_percentage"), False),
        "duplicated_lines": (mb.get("duplicated_lines"), ma.get("duplicated_lines"), False),
        "total_loc": (mb.get("total_loc"), ma.get("total_loc"), None),
        "files_analyzed": (mb.get("files_analyzed"), ma.get("files_analyzed"), None),
        "total_classes": (mb.get("total_classes"), ma.get("total_classes"), None),
        "total_functions": (mb.get("total_functions"), ma.get("total_functions"), None),
        "documentation_score": (cb.get("documentation"), ca.get("documentation"), True),
        "comment_ratio_percent": (_comment_ratio(mods_before), _comment_ratio(mods_after), True),
        "average_risk_score": (mb.get("average_risk_score"), ma.get("average_risk_score"), False),
        "max_risk_score": (mb.get("max_risk_score"), ma.get("max_risk_score"), False),
        "high_risk_modules": (mb.get("high_risk_modules"), ma.get("high_risk_modules"), False),
    }
    metrics = {key: metric_change(p, c, better) for key, (p, c, better) in pairs.items()}
    for key, rule in PROJECT_RULES.items():
        change = metrics[key]
        change["significant"] = _is_significant(change, rule.get("min_abs"), rule.get("min_pct"))
    for key, change in metrics.items():
        change.setdefault("significant", False)
    return metrics


def _category_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    cb, ca = before.get("category_scores") or {}, after.get("category_scores") or {}
    return {key: metric_change(cb.get(key), ca.get(key), True) for key in sorted(set(cb) | set(ca))}


def _language_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    lb, la = before.get("languages") or {}, after.get("languages") or {}
    out = []
    for language in sorted(set(lb) | set(la)):
        b, a = lb.get(language), la.get(language)
        out.append(
            {
                "language": language,
                "status": "added" if b is None else "removed" if a is None else "present",
                "files": metric_change(0 if b is None else b.get("files"), 0 if a is None else a.get("files"), None),
                "loc": metric_change(0 if b is None else b.get("loc"), 0 if a is None else a.get("loc"), None),
            }
        )
    return out


def _project_alerts(metrics: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    regress, improve = [], []
    for key, rule in PROJECT_RULES.items():
        change = metrics[key]
        if not change["significant"]:
            continue
        verb = "rose" if (change["absolute_change"] or 0) > 0 else "fell"
        pct = f" ({change['percent_change']:+.1f}%)" if change["percent_change"] is not None else ""
        item = {
            "scope": "project",
            "id": f"project:{key}",
            "title": f"{rule['label']} {verb}",
            "detail": f"{rule['label']} {verb} from {change['previous']:g} to {change['current']:g}{pct}",
            "severity": _severity(change, rule.get("min_abs"), rule.get("min_pct")),
            "metric": key,
            "previous": change["previous"],
            "current": change["current"],
        }
        (regress if change["direction"] == "regressed" else improve).append(item)
    return regress, improve


# --------------------------------------------------------------------------
# Architecture / API comparison
# --------------------------------------------------------------------------


def _arch_available(report: Mapping[str, Any] | None) -> bool:
    return bool(report) and isinstance(report, Mapping) and "api_mapping" in report


def _by_key(items: Iterable[Mapping[str, Any]], keyfn) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for item in items:
        out.setdefault(keyfn(item), item)
    return out


def _evidence(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(item.get("evidence") or [])


def compare_architecture(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    Compare saved Architecture Intelligence reports. Only what both reports
    actually saved is compared; API findings are matched by their
    "METHOD /path" identity (file paths and line numbers can legitimately change).
    """
    if not (_arch_available(before) and _arch_available(after)):
        missing = []
        if not _arch_available(before):
            missing.append("before")
        if not _arch_available(after):
            missing.append("after")
        return {
            "available": False,
            "reason": "No saved architecture analysis for the " + " and ".join(missing)
            + " version" + ("s" if len(missing) > 1 else "")
            + " (uploaded before Architecture Intelligence existed, or that step failed).",
            "regressions": [],
            "improvements": [],
        }

    mapping_b, mapping_a = before["api_mapping"], after["api_mapping"]

    layers_b = {l["layer"]: l for l in before.get("layers", [])}
    layers_a = {l["layer"]: l for l in after.get("layers", [])}
    layers = []
    for name in list(layers_b) + [n for n in layers_a if n not in layers_b]:
        lb, la = layers_b.get(name), layers_a.get(name)
        layers.append(
            {
                "layer": name,
                "status": "added" if lb is None else "removed" if la is None else "present",
                "files": metric_change(0 if lb is None else lb["files"], 0 if la is None else la["files"], None),
                "loc": metric_change(0 if lb is None else lb["loc"], 0 if la is None else la["loc"], None),
            }
        )

    def ep_key(e): return f"{e['method']} {e['path']}"
    eb, ea = _by_key(before.get("endpoints", []), ep_key), _by_key(after.get("endpoints", []), ep_key)
    cb, ca = _by_key(before.get("calls", []), ep_key), _by_key(after.get("calls", []), ep_key)

    def finding_key(item, side, field):
        part = item.get(side) or {}
        return part.get(field) or f"{item.get('method')} {item.get('path')}"

    unmatched_b = _by_key(mapping_b.get("unmatched_frontend_calls_items", []), lambda i: finding_key(i, "frontend", "call"))
    unmatched_a = _by_key(mapping_a.get("unmatched_frontend_calls_items", []), lambda i: finding_key(i, "frontend", "call"))
    unused_b = _by_key(mapping_b.get("unused_backend_endpoints_items", []), lambda i: finding_key(i, "backend", "endpoint"))
    unused_a = _by_key(mapping_a.get("unused_backend_endpoints_items", []), lambda i: finding_key(i, "backend", "endpoint"))
    matched_b = _by_key(mapping_b.get("matched_items", []), lambda i: f"{finding_key(i, 'frontend', 'call')} -> {finding_key(i, 'backend', 'endpoint')}")
    matched_a = _by_key(mapping_a.get("matched_items", []), lambda i: f"{finding_key(i, 'frontend', 'call')} -> {finding_key(i, 'backend', 'endpoint')}")

    def delta(keys_b, keys_a, store_b, store_a):
        return {
            "added": [{"key": k, "evidence": _evidence(store_a[k])} for k in sorted(keys_a - keys_b)],
            "removed": [{"key": k, "evidence": _evidence(store_b[k])} for k in sorted(keys_b - keys_a)],
        }

    regress: list[dict] = []
    improve: list[dict] = []

    for k in sorted(unmatched_a.keys() - unmatched_b.keys()):
        regress.append(
            {
                "scope": "architecture", "id": f"arch:unmatched:{k}", "severity": "medium",
                "title": f"New potential integration issue: {k}",
                "detail": "A frontend API call with no matching backend endpoint was found in the new version.",
                "evidence": _evidence(unmatched_a[k]),
            }
        )
    for k in sorted(unmatched_b.keys() - unmatched_a.keys()):
        now = "now matched to a backend endpoint" if any(m.startswith(k + " ->") for m in matched_a) else "no longer reported"
        improve.append(
            {
                "scope": "architecture", "id": f"arch:resolved:{k}", "severity": "medium",
                "title": f"Integration issue resolved: {k}",
                "detail": f"A frontend call that had no backend match in the old version is {now}.",
                "evidence": _evidence(unmatched_b[k]),
            }
        )
    for k in sorted(unused_a.keys() - unused_b.keys()):
        regress.append(
            {
                "scope": "architecture", "id": f"arch:unused:{k}", "severity": "low",
                "title": f"Newly potentially unused endpoint: {k}",
                "detail": "No frontend consumer was found for this endpoint in the new version (other clients may use it).",
                "evidence": _evidence(unused_a[k]),
            }
        )
    for k in sorted(unused_b.keys() - unused_a.keys()):
        improve.append(
            {
                "scope": "architecture", "id": f"arch:used:{k}", "severity": "low",
                "title": f"Endpoint no longer reported as unused: {k}",
                "detail": "It now has a frontend consumer, or was removed.",
                "evidence": _evidence(unused_b[k]),
            }
        )

    pt_b, pt_a = before.get("project_type"), after.get("project_type")
    return {
        "available": True,
        "project_type": {"previous": pt_b, "current": pt_a, "changed": pt_b != pt_a},
        "layers": layers,
        "endpoints": {"total": metric_change(len(eb), len(ea), None), **delta(eb.keys(), ea.keys(), eb, ea)},
        "calls": {"total": metric_change(len(cb), len(ca), None), **delta(cb.keys(), ca.keys(), cb, ca)},
        "matched": {"total": metric_change(len(matched_b), len(matched_a), True), **delta(matched_b.keys(), matched_a.keys(), matched_b, matched_a)},
        "unmatched_calls": {"total": metric_change(len(unmatched_b), len(unmatched_a), False), **delta(unmatched_b.keys(), unmatched_a.keys(), unmatched_b, unmatched_a)},
        "unused_endpoints": {"total": metric_change(len(unused_b), len(unused_a), False), **delta(unused_b.keys(), unused_a.keys(), unused_b, unused_a)},
        "uncertain": metric_change(mapping_b.get("uncertain"), mapping_a.get("uncertain"), False),
        "regressions": regress,
        "improvements": improve,
        "note": "API findings are static-analysis observations matched by 'METHOD /path'; they are hints, not runtime facts.",
    }


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def _alert_sort_key(alert: Mapping[str, Any]):
    scope_order = {"project": 0, "file": 1, "architecture": 2}
    return (-_SEVERITY_RANK.get(alert["severity"], 0), scope_order.get(alert["scope"], 9), alert["id"])


def _version_header(version: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version_number": version.get("version_number"),
        "scan_id": version.get("scan_id"),
        "scanned_at": version.get("scanned_at"),
        "is_backfilled": bool(version.get("is_backfilled")),
        "health_score": version.get("health_score"),
        "health_status": version.get("health_status"),
    }


def compare_versions(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """
    Compare two saved versions of one project.

    ``before`` / ``after``: ``{"version": <history row>, "modules": [module
    dicts], "fingerprints": {file_path: sha256}, "architecture": report|None}``.
    ``before`` is the older version (the caller orders them). Returns the full
    comparison; see the module docstring for the guarantees.
    """
    mods_b, mods_a = list(before["modules"]), list(after["modules"])
    hashes_b, hashes_a = dict(before.get("fingerprints") or {}), dict(after.get("fingerprints") or {})

    files = compare_files(mods_b, mods_a, hashes_b, hashes_a)
    code_churn = calculate_code_churn(mods_b, mods_a, hashes_b, hashes_a)
    metrics = _project_metrics(before["version"], after["version"], mods_b, mods_a)
    risk = _risk_distribution(before["version"], after["version"])
    architecture = compare_architecture(before.get("architecture"), after.get("architecture"))

    project_regress, project_improve = _project_alerts(metrics)
    regressions = project_regress + files.pop("_regressions") + architecture.get("regressions", [])
    improvements = project_improve + files.pop("_improvements") + architecture.get("improvements", [])
    regressions.sort(key=_alert_sort_key)
    improvements.sort(key=_alert_sort_key)

    uncertainty: list[str] = []
    for label, side, hashes, mods in (("previous", before, hashes_b, mods_b), ("current", after, hashes_a, mods_a)):
        covered = sum(1 for m in mods if m["file_path"] in hashes)
        if mods and covered == 0:
            uncertainty.append(
                f"The {label} version has no stored file content hashes, so unchanged files can only be "
                "reported as 'unverified' (no tracked metric changed) and exact renames cannot be proven."
            )
        elif mods and covered < len(mods):
            uncertainty.append(f"The {label} version has content hashes for only {covered} of {len(mods)} files.")
        if side["version"].get("is_backfilled"):
            uncertainty.append(
                f"The {label} version was rebuilt from an older scan; some project-level values (for example "
                "duplication percentage) were never stored for it and show as unavailable."
            )
    summary = files["summary"]
    renames = summary["possible_renames"]
    if sum(renames.values()):
        uncertainty.append(
            f"{sum(renames.values())} added/removed file pair(s) look like renames or moves "
            f"({renames['high']} high, {renames['medium']} medium, {renames['low']} low confidence). They are still "
            "counted as added and removed files - renames cannot be confirmed from stored metrics."
        )
    if summary["files_with_missing_data"]:
        uncertainty.append(
            f"{summary['files_with_missing_data']} changed file(s) have metrics that were not recorded or could not be "
            "parsed in one of the versions; rules were applied only to the values that exist."
        )
    uncertainty.extend(files["path_alignment"]["notes"])
    if not architecture["available"]:
        uncertainty.append(architecture["reason"])
    unavailable = [k for k, v in metrics.items() if v["direction"] == "unavailable"]
    if unavailable:
        uncertainty.append("Not recorded in one or both versions: " + ", ".join(sorted(unavailable)) + ".")

    return {
        "schema_version": SCHEMA_VERSION,
        "before": _version_header(before["version"]),
        "after": _version_header(after["version"]),
        "project": {
            "metrics": metrics,
            "categories": _category_changes(before["version"], after["version"]),
            "risk_distribution": risk,
            "languages": _language_changes(before["version"], after["version"]),
            "code_churn": code_churn,
        },
        "files": files,
        "architecture": architecture,
        "regressions": regressions,
        "improvements": improvements,
        "uncertainty": uncertainty,
        "rules": {
            "project": PROJECT_RULES,
            "file": FILE_RULES,
            "risk_levels": RISK_LEVELS,
            "notes": [
                "A change is significant only when it meets the minimum absolute change and, where listed, the minimum percentage change.",
                "A file is 'unchanged' only when its stored content hash is identical; 'unverified' means no tracked metric changed but no hash proves it.",
                "Percentage change is not defined when the previous value is missing or 0.",
            ],
        },
    }
