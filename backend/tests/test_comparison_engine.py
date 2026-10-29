"""
Phase 9B - unit tests of the comparison engine against controlled V1/V2
fixtures (tests/comparison_fixtures.py) whose differences are known exactly.
Pure Python: no database, no HTTP.
"""

import json

from app.analyzer.comparison import (
    align_paths,
    compare_architecture,
    compare_files,
    compare_versions,
    metric_change,
    normalize_path,
)

try:  # pytest imports the module from the tests directory
    import comparison_fixtures as fx
except ImportError:  # pragma: no cover - package-style import
    from tests import comparison_fixtures as fx


def _compare(**kwargs):
    before, after = fx.before_after(**kwargs)
    return compare_versions(before, after)


def _rows(result):
    return {row["path"]: row for row in result["files"]["items"]}


# ------------------------------------------------------- metric arithmetic


def test_metric_change_values_and_percent():
    change = metric_change(70.0, 60.0, True)
    assert change["previous"] == 70.0 and change["current"] == 60.0
    assert change["absolute_change"] == -10.0
    assert change["percent_change"] == -14.29
    assert change["direction"] == "regressed"


def test_metric_change_direction_follows_metric_semantics():
    assert metric_change(3.0, 4.5, False)["direction"] == "regressed"  # complexity up
    assert metric_change(4.5, 3.0, False)["direction"] == "improved"
    assert metric_change(400, 460, None)["direction"] == "changed"  # LOC has no good direction
    assert metric_change(5, 5, True)["direction"] == "unchanged"


def test_zero_denominator_has_no_percentage_and_never_crashes():
    change = metric_change(0, 6, False)
    assert change["absolute_change"] == 6.0
    assert change["percent_change"] is None
    assert "previous value is 0" in change["percent_note"]
    zero_to_zero = metric_change(0, 0, False)
    assert zero_to_zero["direction"] == "unchanged" and zero_to_zero["percent_change"] is None


def test_missing_values_are_unavailable_not_zero():
    for previous, current in ((None, 5), (5, None), (None, None), ("x", 3), (float("nan"), 3)):
        change = metric_change(previous, current, True)
        assert change["direction"] == "unavailable"
        assert change["absolute_change"] is None and change["percent_change"] is None
        assert "not available" in change["percent_note"]


def test_bool_is_not_treated_as_a_number():
    assert metric_change(True, 5, True)["direction"] == "unavailable"


# ------------------------------------------------------ project-level data


def test_project_metrics_previous_current_absolute_percent():
    metrics = _compare()["project"]["metrics"]

    assert (metrics["health_score"]["previous"], metrics["health_score"]["current"]) == (70.0, 60.0)
    assert metrics["health_score"]["absolute_change"] == -10.0

    assert metrics["average_complexity"]["absolute_change"] == 1.5
    assert metrics["average_complexity"]["percent_change"] == 50.0
    assert metrics["average_maintainability"]["absolute_change"] == -6.0
    assert metrics["duplication_percentage"]["absolute_change"] == 3.0
    assert metrics["duplication_percentage"]["percent_change"] == 75.0
    assert metrics["total_loc"]["absolute_change"] == 60 and metrics["total_loc"]["direction"] == "changed"
    assert metrics["files_analyzed"]["absolute_change"] == 1
    assert metrics["documentation_score"]["absolute_change"] == -10.0
    assert metrics["high_risk_modules"]["previous"] == 0 and metrics["high_risk_modules"]["current"] == 3
    assert metrics["high_risk_modules"]["percent_change"] is None  # previous is 0
    assert metrics["average_risk_score"]["direction"] == "regressed"


def test_risk_distribution_levels():
    risk = _compare()["project"]["risk_distribution"]
    assert risk["available"] is True
    levels = risk["levels"]
    assert (levels["low"]["previous"], levels["low"]["current"]) == (6, 4)
    assert (levels["high"]["previous"], levels["high"]["current"]) == (0, 2)
    assert levels["critical"]["absolute_change"] == 1
    assert levels["high"]["direction"] == "regressed" and levels["low"]["direction"] == "regressed"


def test_language_breakdown_changes_include_added_language():
    languages = {l["language"]: l for l in _compare()["project"]["languages"]}
    assert languages["java"]["status"] == "added"
    assert languages["java"]["files"]["current"] == 1 and languages["java"]["files"]["previous"] == 0
    assert languages["python"]["loc"]["absolute_change"] == 40


def test_missing_project_values_are_reported_unavailable():
    before, after = fx.before_after()
    before["version"]["metrics"]["duplication_percentage"] = None
    before["version"]["is_backfilled"] = True
    result = compare_versions(before, after)
    dup = result["project"]["metrics"]["duplication_percentage"]
    assert dup["direction"] == "unavailable" and dup["significant"] is False
    assert any("duplication_percentage" in u for u in result["uncertainty"])
    assert any("rebuilt from an older scan" in u for u in result["uncertainty"])
    assert all(a["id"] != "project:duplication_percentage" for a in result["regressions"])


def test_project_significance_uses_the_documented_thresholds():
    before, after = fx.before_after()
    after["version"]["health_score"] = 66.0  # -4 < min_abs 5
    result = compare_versions(before, after)
    assert result["project"]["metrics"]["health_score"]["significant"] is False
    assert all(a["id"] != "project:health_score" for a in result["regressions"])
    after["version"]["health_score"] = 65.0  # exactly the threshold
    assert compare_versions(before, after)["project"]["metrics"]["health_score"]["significant"] is True


# --------------------------------------------------------- file statuses


def test_every_file_gets_its_expected_status():
    rows = _rows(_compare())
    assert {path: rows[path]["status"] for path in fx.EXPECTED["status"]} == fx.EXPECTED["status"]
    summary = _compare()["files"]["summary"]
    assert (summary["added"], summary["removed"], summary["changed"], summary["unchanged"], summary["unverified"]) == (2, 2, 4, 2, 1)
    assert summary["total_previous"] == 9 and summary["total_current"] == 9


def test_unchanged_is_verified_by_hash_and_metrics_only_is_unverified():
    rows = _rows(_compare())
    assert rows["app/stable.py"]["verification"] == "content-hash"
    assert rows["web/app.ts"]["status"] == "unverified"
    assert rows["web/app.ts"]["verification"] == "metrics-only"
    assert "cannot be verified" in rows["web/app.ts"]["notes"][0]


def test_without_any_hashes_nothing_is_called_verified_unchanged():
    result = _compare(with_hashes=False)
    statuses = {r["path"]: r["status"] for r in result["files"]["items"]}
    assert statuses["app/stable.py"] == "unverified"
    assert "unchanged" not in statuses.values()
    assert any("no stored file content hashes" in u for u in result["uncertainty"])


# ------------------------------------------------- file-level regressions


def test_complexity_regression_and_its_evidence():
    row = _rows(_compare())["app/hot.py"]
    assert row["verdict"] == "regressed"
    rules = {s["rule"]: s for s in row["signals"]}
    assert rules["complexity_total"]["previous"] == 10 and rules["complexity_total"]["current"] == 30
    assert rules["complexity_total"]["percent_change"] == 200.0
    assert rules["complexity_maximum"]["absolute_change"] == 8
    assert rules["risk_level"]["previous"] == "LOW" and rules["risk_level"]["current"] == "MEDIUM"
    assert row["changes"]["complexity_total"]["direction"] == "regressed"


def test_improvement_detected_for_maintainability_and_risk():
    row = _rows(_compare())["app/clean.py"]
    assert row["verdict"] == "improved"
    assert {s["rule"] for s in row["signals"]} == {"maintainability_index", "risk_score", "risk_level"}
    assert all(s["direction"] == "improvement" for s in row["signals"])


def test_coupling_regression():
    row = _rows(_compare())["app/tight.py"]
    assert row["verdict"] == "regressed"
    assert [s["rule"] for s in row["signals"]] == ["coupling"]


def test_risk_band_crossing_is_a_regression_signal():
    row = _rows(_compare())["app/risky.py"]
    assert {s["rule"] for s in row["signals"]} == {"risk_score", "risk_level"}
    assert row["current_risk_level"] == "HIGH" and row["previous_risk_level"] == "MEDIUM"


def test_rules_have_minimum_thresholds_small_changes_are_neutral():
    before, after = fx.before_after()
    after["modules"][1] = fx.mod("app/hot.py", complexity_total=13, complexity_maximum=5)  # +3 < 5
    after["fingerprints"]["app/hot.py"] = "h-hot-2"
    row = _rows(compare_versions(before, after))["app/hot.py"]
    assert row["status"] == "changed" and row["verdict"] == "neutral" and row["signals"] == []


def test_cohesion_and_duplication_rules():
    before = {"version": fx.version_row(1, 1), "modules": [fx.mod("a.py", cohesion=1.0, duplication_percentage=0.0)], "fingerprints": {"a.py": "x"}}
    after = {"version": fx.version_row(2, 2), "modules": [fx.mod("a.py", cohesion=6.0, duplication_percentage=25.0)], "fingerprints": {"a.py": "y"}}
    row = _rows(compare_versions(before, after))["a.py"]
    assert {s["rule"] for s in row["signals"]} == {"cohesion", "duplication_percentage"}
    assert row["verdict"] == "regressed"


def test_mixed_verdict_when_regression_and_improvement_coexist():
    before = {"version": fx.version_row(1, 1), "modules": [fx.mod("a.py", complexity_total=10, maintainability_index=60.0)], "fingerprints": {"a.py": "x"}}
    after = {"version": fx.version_row(2, 2), "modules": [fx.mod("a.py", complexity_total=30, maintainability_index=80.0)], "fingerprints": {"a.py": "y"}}
    assert _rows(compare_versions(before, after))["a.py"]["verdict"] == "mixed"


def test_parse_errors_and_missing_metrics_disable_rules_with_disclosure():
    before = {"version": fx.version_row(1, 1), "modules": [fx.mod("a.py"), fx.mod("b.py", maintainability_index=None)], "fingerprints": {}}
    after = {"version": fx.version_row(2, 2),
             "modules": [fx.mod("a.py", parse_error="SyntaxError", complexity_total=None, maintainability_index=None),
                         fx.mod("b.py", complexity_total=40)], "fingerprints": {}}
    result = compare_versions(before, after)
    rows = _rows(result)
    assert rows["a.py"]["verdict"] == "neutral" and rows["a.py"]["signals"] == []
    assert "parse error" in " ".join(rows["a.py"]["notes"])
    assert "maintainability_index" in " ".join(rows["b.py"]["notes"])  # missing data disclosed
    assert any(s["rule"] == "complexity_total" for s in rows["b.py"]["signals"])  # present metrics still judged
    assert any("not recorded" in u or "parsed" in u for u in result["uncertainty"])


# ---------------------------------------------------------- path handling


def test_normalize_path():
    assert normalize_path("./a\\b//c.py") == "a/b/c.py"
    assert normalize_path("/x/y.py") == "x/y.py"


def test_zip_root_directory_rename_does_not_make_every_file_new():
    plain = _compare()
    renamed = _compare(root_before="proj-v1", root_after="proj-v2")
    assert {r["path"]: r["status"] for r in renamed["files"]["items"]} == {r["path"]: r["status"] for r in plain["files"]["items"]}
    alignment = renamed["files"]["path_alignment"]
    assert alignment["previous_root_ignored"] == "proj-v1" and alignment["current_root_ignored"] == "proj-v2"
    assert alignment["root_changed"] is True
    assert renamed["files"]["summary"]["added"] == 2  # only the genuinely new files
    assert any("ZIP root folder was ignored" in u for u in renamed["uncertainty"])


def test_root_present_on_one_side_only():
    for before_root, after_root in (("proj", ""), ("", "proj")):
        result = _compare(root_before=before_root, root_after=after_root)
        assert result["files"]["summary"]["added"] == 2 and result["files"]["summary"]["removed"] == 2


def test_root_ignored_only_when_it_improves_the_overlap():
    before, after = fx.before_after()
    _, _, info = align_paths(before["modules"], after["modules"])
    assert info["previous_root_ignored"] is None and info["current_root_ignored"] is None


def test_same_relative_path_in_different_directories_is_not_confused():
    before = [fx.mod("a/util.py"), fx.mod("b/util.py", loc_total=99)]
    after = [fx.mod("a/util.py"), fx.mod("b/util.py", loc_total=99)]
    files = compare_files(before, after, {}, {})
    assert files["summary"]["unverified"] == 2 and files["summary"]["added"] == 0


# ---------------------------------------------------------------- renames


def test_exact_rename_is_flagged_high_confidence_but_still_counted_added_removed():
    result = _compare()
    renames = result["files"]["possible_renames"]
    assert renames == [{"from": "app/old_name.py", "to": "app/new_name.py", "confidence": "high", "reason": "identical content hash"}]
    rows = _rows(result)
    assert rows["app/old_name.py"]["status"] == "removed" and rows["app/new_name.py"]["status"] == "added"
    assert "not a confirmed rename" in rows["app/new_name.py"]["notes"][0]
    assert any("look like renames" in u for u in result["uncertainty"])


def test_rename_without_hashes_is_medium_or_low_never_high():
    # keep.py sits at the top level so the folders old/ and new/ are not mistaken for ZIP roots.
    before = [fx.mod("keep.py"), fx.mod("old/util.py", loc_total=60, complexity_total=8), fx.mod("old/other.py", loc_total=100)]
    after = [fx.mod("keep.py"), fx.mod("new/util.py", loc_total=60, complexity_total=8), fx.mod("new/other.py", loc_total=110, complexity_total=99)]
    files = compare_files(before, after, {}, {})
    by_to = {r["to"]: r for r in files["possible_renames"]}
    assert by_to["new/util.py"]["confidence"] == "medium"
    assert by_to["new/other.py"]["confidence"] == "low"
    assert all(r["confidence"] != "high" for r in files["possible_renames"])


def test_unrelated_added_and_removed_files_are_not_paired():
    files = compare_files([fx.mod("a.py", loc_total=10)], [fx.mod("b.py", loc_total=500)], {}, {})  # no shared root: single top-level files
    assert files["possible_renames"] == []


def test_a_high_risk_file_that_may_have_been_renamed_is_not_celebrated_as_removed():
    before = [fx.mod("keep.py"), fx.mod("old/hot.py", risk_score=90.0, risk_level="CRITICAL")]
    after = [fx.mod("keep.py"), fx.mod("new/hot.py", risk_score=90.0, risk_level="CRITICAL")]
    files = compare_files(before, after, {"old/hot.py": "h"}, {"new/hot.py": "h"})
    assert files["_improvements"] == []  # possible rename -> no "risk removed" credit
    assert files["_regressions"][0]["detail"].endswith("(possibly a renamed file - see rename note)")


# -------------------------------------------------------------- alerts


def test_regression_and_improvement_lists_are_sorted_and_explained():
    result = _compare()
    severities = [a["severity"] for a in result["regressions"]]
    ranks = {"high": 3, "medium": 2, "low": 1}
    assert [ranks[s] for s in severities] == sorted((ranks[s] for s in severities), reverse=True)
    ids = {a["id"] for a in result["regressions"]}
    assert {"project:health_score", "file:app/hot.py", "file:app/risky.py", "file:app/tight.py", "added:app/brand_new.py"} <= ids
    assert "file:app/clean.py" in {a["id"] for a in result["improvements"]}
    for alert in result["regressions"] + result["improvements"]:
        assert alert["title"] and alert["detail"] and alert["scope"] in {"project", "file", "architecture"}


def test_thresholds_are_published_with_every_comparison():
    rules = _compare()["rules"]
    assert rules["project"]["health_score"]["min_abs"] == 5.0
    assert {r["id"] for r in rules["file"]} >= {"complexity_total", "maintainability_index", "coupling", "cohesion", "duplication_percentage", "risk_score"}


def test_comparison_is_deterministic_and_json_serializable():
    first, second = _compare(), _compare()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_empty_versions_are_safe():
    result = compare_versions(
        {"version": fx.version_row(1, 1), "modules": [], "fingerprints": {}},
        {"version": fx.version_row(2, 2), "modules": [], "fingerprints": {}},
    )
    assert result["files"]["items"] == [] and result["files"]["summary"]["total_current"] == 0
    assert result["project"]["metrics"]["health_score"]["direction"] == "unchanged"


# ------------------------------------------------------ architecture/API


def test_architecture_comparison_finds_new_endpoint_and_new_integration_issue():
    arch = _compare()["architecture"]
    assert arch["available"] is True
    assert [e["key"] for e in arch["endpoints"]["added"]] == ["DELETE /api/users/{id}"]
    assert arch["endpoints"]["total"]["absolute_change"] == 1
    assert [e["key"] for e in arch["unmatched_calls"]["added"]] == ["POST /api/missing"]
    assert arch["unmatched_calls"]["added"][0]["evidence"][0] == {"side": "frontend", "file": "frontend/api.js", "line": 2, "detail": "POST /api/missing"}
    assert [e["key"] for e in arch["unused_endpoints"]["added"]] == ["DELETE /api/users/{id}"]
    assert arch["matched"]["added"] == [] and arch["matched"]["removed"] == []
    kinds = {a["id"] for a in arch["regressions"]}
    assert kinds == {"arch:unmatched:POST /api/missing", "arch:unused:DELETE /api/users/{id}"}


def test_architecture_issue_resolved_is_an_improvement():
    before, after = fx.before_after()
    before["architecture"], after["architecture"] = fx.arch_report(True, True), fx.arch_report(True, False)
    arch = compare_versions(before, after)["architecture"]
    assert [a["id"] for a in arch["improvements"]] == ["arch:resolved:POST /api/missing"]
    assert arch["unmatched_calls"]["removed"][0]["key"] == "POST /api/missing"


def test_architecture_unavailable_when_either_version_has_none():
    before, after = fx.before_after(with_arch=False)
    arch = compare_architecture(before["architecture"], after["architecture"])
    assert arch["available"] is False and "before and after" in arch["reason"]
    before, after = fx.before_after()
    before["architecture"] = None
    result = compare_versions(before, after)
    assert result["architecture"]["available"] is False
    assert any("No saved architecture analysis" in u for u in result["uncertainty"])
    assert all(a["scope"] != "architecture" for a in result["regressions"])
