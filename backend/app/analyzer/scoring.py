"""
Phase 3: metric normalization -> category scores -> Project Health Score.

IMPORTANT — read before quoting a "score" in the viva:

Every normalization curve and every weight in this module is a **project
design decision for this academic system**, not a universal
software-engineering standard. Where a published reference exists (McCabe
complexity risk bands), it's cited; where none exists for our custom
metric definitions (our coupling/cohesion approximations from Phase 2),
the thresholds are ours, chosen to be monotonic and defensible, and
documented here and in docs/health-score.md.

Pipeline this module implements:

    raw metric (Phase 2)
      -> normalized 0-100 score (higher = better, always)
      -> category score (average of the per-module scores available for
         that category)
      -> weighted Project Health Score (only categories with at least one
         available score contribute; weights are renormalized over just
         those categories rather than pretending a missing category
         scored 100 or 0)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analyzer.engine import ModuleResult, ProjectAnalysis

# ---------------------------------------------------------------------------
# Generic piecewise-linear interpolation helper
# ---------------------------------------------------------------------------


def _piecewise_linear(value: float, points: list[tuple[float, float]]) -> float:
    """
    Map `value` through a monotonic piecewise-linear curve defined by
    `points` (sorted ascending by x). Values below the first anchor or
    above the last are clamped flat to that anchor's y — i.e. the curve
    never extrapolates past its documented range.
    """
    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= value <= x1:
            t = (value - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return points[-1][1]  # unreachable given the guards above


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Per-metric normalization curves
#
# All curves are documented in docs/health-score.md with the same anchor
# points used here, so the two never drift apart.
# ---------------------------------------------------------------------------

# Cyclomatic complexity (module average CC per function/method). Anchors
# are inspired by McCabe's original published risk categories (1976) and
# the widely-cited NIST 500-235 restatement of them: 1-10 "simple, low
# risk", 11-20 "moderate risk", 21-50 "high risk", 50+ "very high risk,
# essentially untestable". We map those bands onto a smooth 0-100 curve
# rather than a hard step function, so two files just either side of a
# boundary don't get wildly different scores.
_COMPLEXITY_POINTS = [(1, 100), (10, 75), (20, 45), (50, 15), (80, 0)]

# Coupling (Phase 2's import/reference-based approximation — see
# docs/metrics.md §5). No published numeric threshold applies to this
# custom definition, so these anchors are a project-defined heuristic:
# "a handful of dependencies is normal and healthy; dozens in one file is
# a red flag," monotonically decreasing.
_COUPLING_POINTS = [(0, 100), (5, 90), (10, 65), (20, 35), (35, 10), (60, 0)]

# Cohesion (LCOM1 — see docs/metrics.md §6). LCOM1 = 0 is the only value
# with an agreed meaning ("perfectly cohesive by this measure"); there is
# no standard mapping of larger LCOM1 values onto 0-100, so again this is
# a project-defined, monotonically-decreasing heuristic.
_COHESION_POINTS = [(0, 100), (2, 80), (5, 55), (10, 25), (20, 0)]

# Documentation proxy: comment-to-code line ratio (percent), from Phase
# 2's LOC metric. 20% is used as the "fully credited" ratio — commonly
# cited as a healthy commenting density in code-quality literature — with
# a flat plateau at 100 beyond that (more comments past this point is not
# treated as strictly better, to avoid rewarding comment-padding).
_DOCUMENTATION_TARGET_RATIO_PERCENT = 20.0


@dataclass
class NormalizedModuleScores:
    """0-100 scores for one module. Any component that could not be
    computed (its Phase 2 raw metric was null/undefined for this file) is
    None — never a fabricated number."""

    maintainability: float | None
    complexity: float | None
    coupling: float | None
    cohesion: float | None
    duplication: float | None
    documentation: float | None


def score_maintainability(mi: float | None) -> float | None:
    """MI is already a documented 0-100 scale where higher = better
    (see docs/metrics.md §4) — no further transform needed beyond
    clamping for safety."""
    if mi is None:
        return None
    return round(_clamp(mi), 2)


def score_complexity(avg_complexity: float, has_functions: bool) -> float | None:
    """None when the file has no functions/methods at all — there is
    nothing to have a "how complex are this file's functions" opinion
    about, so we don't invent one (a perfect 100 would misleadingly imply
    'simple code' rather than 'no code to measure')."""
    if not has_functions:
        return None
    return round(_piecewise_linear(avg_complexity, _COMPLEXITY_POINTS), 2)


def score_coupling(coupling: int | None) -> float | None:
    if coupling is None:
        return None
    return round(_piecewise_linear(coupling, _COUPLING_POINTS), 2)


def score_cohesion(cohesion: float | None) -> float | None:
    """Already None from Phase 2 when cohesion can't be reliably computed
    (fewer than 2 methods, or no fields) — passed through as None."""
    if cohesion is None:
        return None
    return round(_piecewise_linear(cohesion, _COHESION_POINTS), 2)


def score_duplication(duplication_percentage: float | None) -> float | None:
    """Linear inverse — no threshold curve needed since duplication
    percentage is already a clean 0-100 quantity: 0% duplication -> 100
    score, 100% duplication -> 0 score."""
    if duplication_percentage is None:
        return None
    return round(_clamp(100.0 - duplication_percentage), 2)


def score_documentation(comment_lines: int, code_lines: int) -> float | None:
    """None when the file has zero code lines — a comment ratio isn't
    meaningful for a file with no code to document."""
    if code_lines <= 0:
        return None
    ratio_percent = (comment_lines / code_lines) * 100
    score = (ratio_percent / _DOCUMENTATION_TARGET_RATIO_PERCENT) * 100
    return round(_clamp(score), 2)


def score_module(module: ModuleResult) -> NormalizedModuleScores:
    """Normalize every available metric for one module. A module with a
    parse error gets all-None scores — its raw metrics were never
    computed, so there is nothing honest to normalize."""
    if module.parse_error is not None:
        return NormalizedModuleScores(None, None, None, None, None, None)

    return NormalizedModuleScores(
        maintainability=score_maintainability(module.maintainability_index),
        complexity=score_complexity(module.complexity_average, module.functions > 0),
        coupling=score_coupling(module.coupling),
        cohesion=score_cohesion(module.cohesion),
        duplication=score_duplication(module.duplication_percentage),
        documentation=score_documentation(module.loc.comments, module.loc.code),
    )


def _average_available(values: list[float | None]) -> float | None:
    available = [v for v in values if v is not None]
    if not available:
        return None
    return round(sum(available) / len(available), 2)


# ---------------------------------------------------------------------------
# Category scores + Project Health Score
# ---------------------------------------------------------------------------

# These are OUR PROJECT DESIGN WEIGHTS for this academic system's Health
# Score, matching the master specification's initial weights — NOT a
# universal software-engineering standard. See docs/health-score.md.
HEALTH_SCORE_WEIGHTS: dict[str, float] = {
    "maintainability": 0.20,
    "complexity": 0.20,
    "code_quality": 0.15,
    "testing": 0.15,
    "coupling_cohesion": 0.10,
    "duplication": 0.10,
    "documentation": 0.05,
    "code_churn": 0.05,
}

HEALTH_STATUS_BANDS: list[tuple[float, float, str]] = [
    (0, 39, "CRITICAL"),
    (40, 59, "POOR"),
    (60, 74, "NEEDS_ATTENTION"),
    (75, 89, "HEALTHY"),
    (90, 100, "EXCELLENT"),
]


def health_status_for_score(score: float) -> str:
    for low, high, label in HEALTH_STATUS_BANDS:
        if low <= score <= high:
            return label
    return "EXCELLENT" if score > 100 else "CRITICAL"  # defensive fallback


@dataclass
class CategoryScores:
    maintainability: float | None
    complexity: float | None
    code_quality: float | None
    testing: float | None
    coupling_cohesion: float | None
    duplication: float | None
    documentation: float | None
    code_churn: float | None

    def to_dict(self) -> dict:
        return {
            "maintainability": self.maintainability,
            "complexity": self.complexity,
            "code_quality": self.code_quality,
            "testing": self.testing,
            "coupling_cohesion": self.coupling_cohesion,
            "duplication": self.duplication,
            "documentation": self.documentation,
            "code_churn": self.code_churn,
        }


@dataclass
class ProjectHealth:
    health_score: float
    health_status: str
    category_scores: CategoryScores
    unavailable_categories: list[str]

    def to_dict(self) -> dict:
        return {
            "health_score": self.health_score,
            "health_status": self.health_status,
            "category_scores": self.category_scores.to_dict(),
            "unavailable_categories": self.unavailable_categories,
        }


def calculate_project_health(
    analysis: ProjectAnalysis,
    module_scores: list[NormalizedModuleScores],
    code_churn_score: float | None = None,
) -> ProjectHealth:
    """
    Aggregate per-module normalized scores into category scores, then into
    a single weighted Health Score.

    Testing uses a deterministic static test-file presence score. Uploaded
    source is never executed, so this is intentionally NOT line/branch
    coverage. Code Churn is supplied by the upload flow when a previous
    version exists; it is unavailable for a project's first version.
    Missing categories are excluded and their weights are renormalized over
    what is actually available.
    """
    maintainability = _average_available([s.maintainability for s in module_scores])
    complexity = _average_available([s.complexity for s in module_scores])
    coupling = _average_available([s.coupling for s in module_scores])
    cohesion = _average_available([s.cohesion for s in module_scores])
    duplication = _average_available([s.duplication for s in module_scores])
    documentation = _average_available([s.documentation for s in module_scores])

    # Code Quality: composite of the two most relevant available signals
    # (maintainability, duplication) — Phase 2 has no separate lint/smell
    # count, so we don't invent a third input. Documented in
    # docs/health-score.md as a deliberate, named simplification.
    code_quality = _average_available([maintainability, duplication])

    # Coupling/Cohesion: composite of the two, same reasoning as above.
    coupling_cohesion = _average_available([coupling, cohesion])

    # Static testing signal: source is never executed, so this is a
    # test-file presence score rather than claimed runtime coverage.
    testing = analysis.testing_score
    code_churn = code_churn_score

    category_values = {
        "maintainability": maintainability,
        "complexity": complexity,
        "code_quality": code_quality,
        "testing": testing,
        "coupling_cohesion": coupling_cohesion,
        "duplication": duplication,
        "documentation": documentation,
        "code_churn": code_churn,
    }

    available = {k: v for k, v in category_values.items() if v is not None}
    unavailable = [k for k, v in category_values.items() if v is None]

    if available:
        weight_sum = sum(HEALTH_SCORE_WEIGHTS[k] for k in available)
        weighted_sum = sum(HEALTH_SCORE_WEIGHTS[k] * v for k, v in available.items())
        health_score = round(weighted_sum / weight_sum, 2)
    else:
        # No category could be scored at all (e.g. every file failed to
        # parse). 0 is the honest answer, not a fabricated default.
        health_score = 0.0

    return ProjectHealth(
        health_score=health_score,
        health_status=health_status_for_score(health_score),
        category_scores=CategoryScores(
            maintainability=maintainability,
            complexity=complexity,
            code_quality=code_quality,
            testing=testing,
            coupling_cohesion=coupling_cohesion,
            duplication=duplication,
            documentation=documentation,
            code_churn=code_churn,
        ),
        unavailable_categories=unavailable,
    )
