"""
Phase 3: transparent, rule-based Module Risk Detection.

Explicitly NOT an LLM and NOT ML — per the Phase 3 spec, this is a
deterministic scoring function over the same normalized metric scores
computed in scoring.py. Two runs on the same input always produce the same
risk score.

    risk_score = 100 - (weighted average of a module's available
                         normalized quality scores)

using a project-defined set of risk weights (documented below and in
docs/health-score.md) distinct from — though similar in spirit to — the
Health Score category weights, because risk is about *this one file*
rather than the whole project, and Phase 2 gives us no test-coverage
signal to include the way the spec's example risk-factor list mentions.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analyzer.engine import ModuleResult
from app.analyzer.scoring import NormalizedModuleScores

# Project-defined weights for module-level risk, NOT a universal standard.
# Complexity and maintainability are weighted highest because they most
# directly predict how hard a module is to safely change; coupling,
# cohesion, and duplication each get an equal, smaller share. Test
# coverage is deliberately absent — Phase 2 has no coverage data source,
# so it is never included even when other weights are renormalized.
RISK_WEIGHTS: dict[str, float] = {
    "complexity": 0.30,
    "maintainability": 0.25,
    "coupling": 0.15,
    "cohesion": 0.15,
    "duplication": 0.15,
}

RISK_LEVEL_BANDS: list[tuple[float, float, str]] = [
    (0, 29, "LOW"),
    (30, 59, "MEDIUM"),
    (60, 79, "HIGH"),
    (80, 100, "CRITICAL"),
]

# A component scoring below this (on the same 0-100 "higher = better"
# scale scoring.py produces) is called out as a specific risk factor.
# 50 is the midpoint of the 0-100 scale — a documented, simple choice for
# "below average," not a metric-specific tuned threshold.
_RISK_FACTOR_THRESHOLD = 50.0

_RISK_FACTOR_LABELS: dict[str, str] = {
    "complexity": "High cyclomatic complexity",
    "maintainability": "Low maintainability",
    "coupling": "High coupling",
    "cohesion": "Low cohesion",
    "duplication": "High duplication",
}


@dataclass
class ModuleRisk:
    file_name: str
    file_path: str
    risk_score: float | None
    risk_level: str | None
    risk_factors: list[str]

    def to_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "file_path": self.file_path,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "risk_factors": self.risk_factors,
        }


def risk_level_for_score(score: float) -> str:
    for low, high, label in RISK_LEVEL_BANDS:
        if low <= score <= high:
            return label
    return "CRITICAL" if score > 100 else "LOW"  # defensive fallback


def calculate_module_risk(module: ModuleResult, scores: NormalizedModuleScores) -> ModuleRisk:
    if module.parse_error is not None:
        return ModuleRisk(
            file_name=module.file_name,
            file_path=module.file_path,
            risk_score=None,
            risk_level=None,
            risk_factors=["File could not be parsed — metrics unavailable."],
        )

    components = {
        "complexity": scores.complexity,
        "maintainability": scores.maintainability,
        "coupling": scores.coupling,
        "cohesion": scores.cohesion,
        "duplication": scores.duplication,
    }
    available = {k: v for k, v in components.items() if v is not None}

    if not available:
        # Every risk-relevant metric was undefined for this file (e.g. a
        # file with no functions/classes at all). We report "no risk
        # signal available" rather than guessing.
        return ModuleRisk(
            file_name=module.file_name,
            file_path=module.file_path,
            risk_score=None,
            risk_level=None,
            risk_factors=["Not enough structure in this file to assess risk."],
        )

    weight_sum = sum(RISK_WEIGHTS[k] for k in available)
    weighted_quality = sum(RISK_WEIGHTS[k] * v for k, v in available.items()) / weight_sum
    risk_score = round(100.0 - weighted_quality, 2)

    risk_factors = [
        _RISK_FACTOR_LABELS[k]
        for k, v in available.items()
        if v < _RISK_FACTOR_THRESHOLD
    ]

    return ModuleRisk(
        file_name=module.file_name,
        file_path=module.file_path,
        risk_score=risk_score,
        risk_level=risk_level_for_score(risk_score),
        risk_factors=risk_factors,
    )


def rank_risky_modules(module_risks: list[ModuleRisk]) -> list[dict]:
    """Descending by risk_score; modules with no risk score (parse errors
    or no scorable structure) are excluded from the ranking rather than
    sorted arbitrarily."""
    scored = [r for r in module_risks if r.risk_score is not None]
    scored.sort(key=lambda r: r.risk_score, reverse=True)
    return [
        {"file_name": r.file_name, "risk_score": r.risk_score, "risk_level": r.risk_level}
        for r in scored
    ]
