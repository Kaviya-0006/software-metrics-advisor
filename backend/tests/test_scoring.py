"""
Tests for Phase 3 scoring: metric normalization, category scores, Health
Score, and health status boundaries.
"""

from app.analyzer.engine import ModuleResult
from app.analyzer.metrics.loc import LocResult
from app.analyzer.metrics.testing import calculate_testing_score, is_test_path
from app.analyzer.scoring import (
    calculate_project_health,
    health_status_for_score,
    score_cohesion,
    score_complexity,
    score_coupling,
    score_documentation,
    score_duplication,
    score_maintainability,
    score_module,
)


def _module(**overrides) -> ModuleResult:
    defaults = dict(
        file_name="f.py", file_path="f.py", language="python",
        loc=LocResult(total=100, code=80, comments=10, blank=10),
        classes=1, functions=5,
        complexity_total=10, complexity_average=2.0, complexity_maximum=4,
        maintainability_index=70.0,
        coupling=2, cohesion=0.0, depth_of_inheritance=1,
        duplication_percentage=0.0, duplicated_lines=0,
        parse_error=None,
    )
    defaults.update(overrides)
    return ModuleResult(**defaults)


# --- Individual normalization functions -------------------------------------

def test_maintainability_score_passes_through_clamped():
    assert score_maintainability(70.0) == 70.0
    assert score_maintainability(150.0) == 100.0  # clamped
    assert score_maintainability(-10.0) == 0.0
    assert score_maintainability(None) is None


def test_complexity_score_high_gives_lower_score_than_low():
    low = score_complexity(2.0, has_functions=True)
    high = score_complexity(40.0, has_functions=True)
    assert low > high


def test_complexity_score_none_when_no_functions():
    assert score_complexity(0.0, has_functions=False) is None


def test_coupling_score_high_gives_lower_score_than_low():
    low = score_coupling(1)
    high = score_coupling(50)
    assert low > high
    assert score_coupling(None) is None


def test_cohesion_score_high_lcom_gives_lower_score():
    good = score_cohesion(0.0)
    bad = score_cohesion(15.0)
    assert good > bad
    assert score_cohesion(None) is None


def test_duplication_score_is_inverse_of_percentage():
    assert score_duplication(0.0) == 100.0
    assert score_duplication(100.0) == 0.0
    assert score_duplication(25.0) == 75.0
    assert score_duplication(None) is None


def test_duplication_high_gives_lower_score_than_low():
    assert score_duplication(10.0) > score_duplication(90.0)


def test_documentation_score_none_when_no_code():
    assert score_documentation(comment_lines=5, code_lines=0) is None


def test_documentation_score_reaches_full_at_target_ratio():
    # target ratio is 20% comments-to-code
    assert score_documentation(comment_lines=20, code_lines=100) == 100.0
    assert score_documentation(comment_lines=0, code_lines=100) == 0.0



def test_testing_score_detects_common_test_paths():
    score, test_files, production_files = calculate_testing_score([
        "src/app.py", "src/service.py", "tests/test_app.py", "tests/test_service.py",
    ])
    assert score == 100.0
    assert test_files == 2
    assert production_files == 2
    assert is_test_path("__tests__/api.test.js")
    assert is_test_path("src/UserServiceTest.java") is False


def test_testing_score_is_zero_without_tests():
    score, test_files, production_files = calculate_testing_score(["src/a.py", "src/b.py"])
    assert score == 0.0
    assert test_files == 0
    assert production_files == 2

# --- Missing/null metric handling -------------------------------------------

def test_score_module_all_none_on_parse_error():
    module = _module(parse_error="broken")
    scores = score_module(module)
    assert scores.maintainability is None
    assert scores.complexity is None
    assert scores.coupling is None
    assert scores.cohesion is None
    assert scores.duplication is None
    assert scores.documentation is None


def test_score_module_handles_missing_cohesion():
    module = _module(cohesion=None, functions=0)
    scores = score_module(module)
    assert scores.cohesion is None
    assert scores.complexity is None  # no functions either


# --- Health score + category aggregation ------------------------------------

def _fake_analysis():
    # calculate_project_health only reads .modules count-independent
    # fields via the module_scores list, so a minimal stand-in works.
    from app.analyzer.engine import ProjectAnalysis
    return ProjectAnalysis(
        modules=[], files_analyzed=0, languages=[], total_classes=0,
        total_functions=0, total_loc=0, total_complexity=0,
        average_complexity=0.0, average_maintainability=None,
        average_coupling=0.0, duplication_percentage=0.0,
        duplicated_lines=0, files_with_errors=0,
    )


def test_health_score_testing_uses_static_test_presence_and_churn_is_unavailable():
    module = _module()
    scores = [score_module(module)]
    analysis = _fake_analysis()
    analysis.testing_score = 50.0
    health = calculate_project_health(analysis, scores)
    assert health.category_scores.testing == 50.0
    assert health.category_scores.code_churn is None
    assert "testing" not in health.unavailable_categories
    assert "code_churn" in health.unavailable_categories


def test_health_score_high_quality_module_scores_well():
    good_module = _module(
        maintainability_index=95.0, complexity_average=1.0, coupling=1,
        cohesion=0.0, duplication_percentage=0.0,
        loc=LocResult(total=100, code=100, comments=20, blank=0),
    )
    scores = [score_module(good_module)]
    health = calculate_project_health(_fake_analysis(), scores)
    assert health.health_score > 75


def test_health_score_low_quality_module_scores_poorly():
    bad_module = _module(
        maintainability_index=10.0, complexity_average=60.0, coupling=50,
        cohesion=20.0, duplication_percentage=90.0,
        loc=LocResult(total=100, code=100, comments=0, blank=0),
    )
    scores = [score_module(bad_module)]
    health = calculate_project_health(_fake_analysis(), scores)
    assert health.health_score < 40


def test_health_score_no_modules_scores_zero_not_fabricated():
    health = calculate_project_health(_fake_analysis(), [])
    assert health.health_score == 0.0
    assert health.health_status == "CRITICAL"


# --- Health status boundaries ------------------------------------------------

def test_health_status_boundaries():
    assert health_status_for_score(0) == "CRITICAL"
    assert health_status_for_score(39) == "CRITICAL"
    assert health_status_for_score(40) == "POOR"
    assert health_status_for_score(59) == "POOR"
    assert health_status_for_score(60) == "NEEDS_ATTENTION"
    assert health_status_for_score(74) == "NEEDS_ATTENTION"
    assert health_status_for_score(75) == "HEALTHY"
    assert health_status_for_score(89) == "HEALTHY"
    assert health_status_for_score(90) == "EXCELLENT"
    assert health_status_for_score(100) == "EXCELLENT"
