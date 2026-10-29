"""
Tests for Phase 3 risk detection: risk score, risk level boundaries, risk
factor detection, and risky-module ranking.
"""

from app.analyzer.engine import ModuleResult
from app.analyzer.metrics.loc import LocResult
from app.analyzer.risk import (
    calculate_module_risk,
    rank_risky_modules,
    risk_level_for_score,
)
from app.analyzer.scoring import score_module


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


def test_risk_level_boundaries():
    assert risk_level_for_score(0) == "LOW"
    assert risk_level_for_score(29) == "LOW"
    assert risk_level_for_score(30) == "MEDIUM"
    assert risk_level_for_score(59) == "MEDIUM"
    assert risk_level_for_score(60) == "HIGH"
    assert risk_level_for_score(79) == "HIGH"
    assert risk_level_for_score(80) == "CRITICAL"
    assert risk_level_for_score(100) == "CRITICAL"


def test_healthy_module_has_low_risk():
    module = _module(
        maintainability_index=95.0, complexity_average=1.0, coupling=1,
        cohesion=0.0, duplication_percentage=0.0,
    )
    risk = calculate_module_risk(module, score_module(module))
    assert risk.risk_score < 30
    assert risk.risk_level == "LOW"
    assert risk.risk_factors == []


def test_unhealthy_module_has_high_risk_and_factors():
    module = _module(
        maintainability_index=10.0, complexity_average=60.0, coupling=50,
        cohesion=20.0, duplication_percentage=90.0,
    )
    risk = calculate_module_risk(module, score_module(module))
    assert risk.risk_score >= 60
    assert risk.risk_level in ("HIGH", "CRITICAL")
    assert "High cyclomatic complexity" in risk.risk_factors
    assert "Low maintainability" in risk.risk_factors
    assert "High coupling" in risk.risk_factors
    assert "High duplication" in risk.risk_factors


def test_risk_factors_only_include_supported_metrics():
    # Cohesion undefined (no functions -> also no complexity signal);
    # factors list must never mention cohesion/complexity here.
    module = _module(functions=0, cohesion=None, coupling=50, maintainability_index=10.0)
    risk = calculate_module_risk(module, score_module(module))
    assert "Low cohesion" not in risk.risk_factors
    assert "High cyclomatic complexity" not in risk.risk_factors
    assert "High coupling" in risk.risk_factors


def test_parse_error_module_has_no_risk_score():
    module = _module(parse_error="syntax error")
    risk = calculate_module_risk(module, score_module(module))
    assert risk.risk_score is None
    assert risk.risk_level is None
    assert risk.risk_factors  # explains why


def test_rank_risky_modules_sorts_descending_and_excludes_unscored():
    modules = [
        _module(file_name="low.py", maintainability_index=95.0, complexity_average=1.0,
                coupling=1, cohesion=0.0, duplication_percentage=0.0),
        _module(file_name="high.py", maintainability_index=10.0, complexity_average=60.0,
                coupling=50, cohesion=20.0, duplication_percentage=90.0),
        _module(file_name="broken.py", parse_error="bad syntax"),
    ]
    risks = [calculate_module_risk(m, score_module(m)) for m in modules]
    ranking = rank_risky_modules(risks)

    assert [r["file_name"] for r in ranking] == ["high.py", "low.py"]
    assert ranking[0]["risk_score"] >= ranking[1]["risk_score"]
    assert all(r["file_name"] != "broken.py" for r in ranking)
