# Project Health Score & Risk Detection (Phase 3)

**These health-score weights and thresholds are project-defined
heuristics for this academic system, not universal software-engineering
standards.** Where a published reference genuinely exists (McCabe's
complexity risk bands), it's cited below; everywhere else, the curve or
weight is a documented design choice made for this project, chosen to be
monotonic, transparent, and defensible in the viva — not received wisdom.

This document explains every formula behind `POST /api/projects/upload`'s
`health`, `category_scores`, and `risky_modules` output. Implementation:
`backend/app/analyzer/scoring.py` (normalization + health score) and
`backend/app/analyzer/risk.py` (module risk).

Phase 3 is explicitly **rule-based** — no LLM, no ML. Every number here is
reproducible by hand from the Phase 2 raw metrics (`docs/metrics.md`). The
AI Advisor that explains these numbers in plain language is a later
phase.

---

## 1. Pipeline

```
raw metric (Phase 2, per module)
  -> normalized 0-100 score (higher = always better)
  -> category score (average of per-module scores available in that category)
  -> weighted Project Health Score (only available categories contribute)

raw metrics (per module)
  -> normalized 0-100 scores (same functions as above)
  -> weighted module risk score (100 - weighted quality)
  -> risk level + risk factors
  -> project-wide risky-module ranking
```

The same normalization functions feed both the Health Score (project-wide
average) and the Risk Score (per-module weighted composite) — one set of
formulas, two aggregations, so the two numbers can never quietly disagree
about what "good" means for a given metric.

---

## 2. Metric Normalization (0-100, higher = better)

| Raw metric | Direction | Method |
|---|---|---|
| Maintainability Index | high = good | Already 0-100 (see `docs/metrics.md` §4) — passed through, clamped. |
| Cyclomatic Complexity (module average) | low = good | Piecewise-linear curve, anchors `(1,100) (10,75) (20,45) (50,15) (80,0)`. |
| Coupling | low = good | Piecewise-linear curve, anchors `(0,100) (5,90) (10,65) (20,35) (35,10) (60,0)`. |
| Cohesion (LCOM1) | low = good | Piecewise-linear curve, anchors `(0,100) (2,80) (5,55) (10,25) (20,0)`. |
| Code Duplication % | low = good | Linear inverse: `score = 100 - duplication_percentage`. |
| Documentation (comment/code ratio) | high = good, capped | `score = min(100, (comment_lines/code_lines*100) / 20 * 100)` — 20% comment density earns full credit; more isn't scored higher (avoids rewarding comment-padding). |
| Testing Score | high = good | Static test-file presence proxy: one test source file for every production source file earns 100, capped at 100. Uploaded source is never executed, so this is **not** line/branch coverage. |

**Why piecewise-linear curves for complexity/coupling/cohesion, not a
simple linear scale or hard cutoffs?** A hard cutoff (e.g. "CC>20 = 0
points") makes two files scoring 19 and 21 look wildly different despite
being nearly identical in practice. A pure linear scale from best-possible
to some max would need an arbitrary max anyway, and treats the difference
between CC=2 and CC=8 (both "fine" in practice) the same as CC=40 and
CC=46 (both "bad"). Piecewise-linear anchors let the curve be flat where
the metric doesn't really distinguish quality (very low complexity is all
"fine") and steep where it does (moderate-to-high complexity), matching
how these metrics are discussed in the literature (bands, not a single
formula) while still producing a smooth, continuous 0-100 score.

**Complexity anchors specifically** are inspired by McCabe's original
published risk categories (1976) and their widely-cited restatement in
NIST Special Publication 500-235: CC 1-10 "simple, low risk", 11-20
"moderate risk", 21-50 "high risk", 50+ "very high risk, essentially
untestable." Coupling and cohesion have no equivalent published standard
for the custom metric *definitions* this project uses (see
`docs/metrics.md` §5-6 for why Phase 2's coupling/cohesion are themselves
approximations) — their anchors are project-defined.

### Handling missing/undefined metrics

A metric normalizes to `None` (never a fabricated number) when:
- the module failed to parse (`parse_error` set) — **all** six scores are `None`.
- Complexity: the module has zero functions/methods — nothing to average.
- Cohesion: already `None` from Phase 2 (fewer than 2 methods, or no fields).
- Documentation: the module has zero code lines.
- Coupling, Maintainability, Duplication: `None` only if the underlying
  Phase 2 value itself was `None` (in practice these are rarely null for
  a successfully-parsed file).

---

## 3. Category Scores

| Category | Computed as |
|---|---|
| Maintainability | Average of module Maintainability scores. |
| Complexity | Average of module Complexity scores. |
| Code Quality | Average of `(Maintainability category, Duplication category)` — Phase 2 has no separate lint/code-smell count, so Code Quality is a named composite of the two most relevant available signals rather than an invented third metric. |
| Testing | Static test-file presence score from the uploaded source tree. |
| Coupling/Cohesion | Average of `(Coupling category, Cohesion category)`. |
| Duplication | Average of module Duplication scores. |
| Documentation | Average of module Documentation scores. |
| Code Churn | **Version-to-version churn** — available from V2 onward using stored file changes; V1 is unavailable because there is no previous baseline. |

Each category average is computed only over modules where that specific
score is available (`None`s excluded, not treated as zero). If **no**
module has an available score for a category, the category itself is
`None` in the API response, not a fabricated value.

---

## 3A. Code Churn

Code Churn is a version-to-version signal, so the first version of a project
has no Code Churn score. Starting with V2, the system compares the current
upload with the immediately previous version.

The stored file comparison classifies files as added, modified, removed,
unchanged, or unverified. For the churn calculation:

- **Added LOC** = LOC of added files.
- **Removed LOC** = LOC of removed files.
- **Modified LOC** = the smaller LOC value of each modified file's previous
  and current versions. This is a conservative estimate of existing code
  touched by a changed file; it is not a line-by-line diff.
- **Churned LOC** = added LOC + modified LOC + removed LOC.
- **Churn rate** = churned LOC / previous-version LOC × 100.
- **Code Churn score** = 100 − churn rate, capped at a minimum of 0.

Therefore, a lower amount of version-to-version change produces a higher
Code Churn category score. The score is deterministic and uses only data
already stored by the application; source code is never executed.

## 4. Project Health Score

**Initial weights** (from the project master specification — explicitly
our own design, not an industry standard):

| Category | Weight |
|---|---|
| Maintainability | 20% |
| Complexity | 20% |
| Code Quality | 15% |
| Testing | 15% |
| Coupling/Cohesion | 10% |
| Duplication | 10% |
| Documentation | 5% |
| Code Churn | 5% |

**Formula**, using only categories with an available score:

```
health_score = ( Σ weight_i * category_score_i ) / ( Σ weight_i )
                for every category i that is available
```

This is a **renormalized weighted average**, not "missing categories
count as 0" or "missing categories count as 100" — both of those would
misrepresent a project that simply has no test-coverage data as either
artificially penalized or artificially rewarded. In the current system,
Testing contributes when source files are available because its static
test-file presence signal can be measured without executing uploaded code.
Code Churn (5%) is included when version-to-version change data is
available.

If literally no category can be scored (e.g. every uploaded file failed
to parse), `health_score = 0.0` — an honest "we have nothing to go on",
not a fabricated default.

Weights are defined in `HEALTH_SCORE_WEIGHTS` in `scoring.py` and are
kept as a single named constant, not hard-coded inline, so they can be
tuned in one place if the course project's design changes.

---

## 5. Health Status

| Score range | Status |
|---|---|
| 0-39 | CRITICAL |
| 40-59 | POOR |
| 60-74 | NEEDS_ATTENTION |
| 75-89 | HEALTHY |
| 90-100 | EXCELLENT |

Boundaries are defined as data (`HEALTH_STATUS_BANDS` in `scoring.py`),
not scattered `if` statements, so they can be adjusted without touching
the scoring logic itself.

---

## 6. Module Risk Score

Deterministic, rule-based — **no LLM, no ML**. Formula:

```
risk_score = 100 - ( Σ risk_weight_i * quality_score_i ) / ( Σ risk_weight_i )
              for every metric i available for this specific module
```

**Risk weights** (project-defined, distinct from but similar in spirit to
the Health Score category weights — chosen because complexity and
maintainability most directly predict how hard a module is to safely
modify):

| Metric | Weight |
|---|---|
| Complexity | 30% |
| Maintainability | 25% |
| Coupling | 15% |
| Cohesion | 15% |
| Duplication | 15% |

Test coverage is not included — there is no coverage signal to weight,
and it is never substituted with a guess. Weights are renormalized over
whichever of the five are actually available *for that module* (e.g. a
module with no classes has no cohesion score, so risk is computed from
the remaining four, reweighted to sum to 1.0).

If **none** of the five metrics are available for a module (parse error,
or a file with no functions/classes at all — nothing to assess), the
module's `risk_score` and `risk_level` are `null`, with a `risk_factors`
entry explaining why, rather than a fabricated risk value.

---

## 7. Risk Levels

| Score range | Level |
|---|---|
| 0-29 | LOW |
| 30-59 | MEDIUM |
| 60-79 | HIGH |
| 80-100 | CRITICAL |

---

## 8. Risk Factors

A specific risk factor is listed for a module when that metric's
normalized score falls **below 50** (the midpoint of the 0-100 scale — a
simple, documented "below average" threshold, not individually tuned per
metric):

| Condition | Factor text |
|---|---|
| Complexity score < 50 | "High cyclomatic complexity" |
| Maintainability score < 50 | "Low maintainability" |
| Coupling score < 50 | "High coupling" |
| Cohesion score < 50 | "Low cohesion" |
| Duplication score < 50 | "High duplication" |

Only factors for metrics that were actually computable for that module
appear — a module with no classes (so cohesion is `None`) never gets a
"Low cohesion" factor it has no evidence for.

---

## 9. Risky Module Ranking

`risky_modules` in the API response is every module with a non-null risk
score, sorted descending by `risk_score`. Modules that couldn't be scored
(parse errors, or no assessable structure) are excluded from the ranking
— they are still visible in `analysis.modules[]` with `risk_score: null`
and an explanatory `risk_factors` entry, just not placed in a numeric
ranking that would misrepresent them as "low risk."

---

## 10. Limitations (read alongside `docs/metrics.md`)

- **Testing is now available as a static test-file presence signal.** It does
  not claim runtime line/branch coverage because uploaded source is never
  executed. **Code Churn is unavailable for a first version**; later versions use
  change data is incorporated.
- **Documentation score is a comment-density proxy**, not an assessment
  of whether comments are *accurate* or *useful* — a file with 20%
  comment lines that are all stale or wrong scores identically to one
  with 20% genuinely helpful comments.
- **Code Quality and Coupling/Cohesion are named composites** of other
  already-scored categories, not independently-measured categories —
  this is a deliberate, documented simplification given what Phase 2
  actually measures, not a hidden extra metric.
- **All normalization curves and weights are project-defined** except
  where a citation is given (McCabe/NIST for complexity). They encode
  this project's judgment about what "good" looks like for an academic
  demonstration system, not a peer-reviewed or industry-standardized
  scoring methodology.
- **Phase 3 is entirely rule-based.** No AI, no LLM, no machine learning
  is used to compute health_score, category_scores, risk_score,
  risk_level, or risk_factors — every value here can be recomputed by
  hand from the Phase 2 metrics using the formulas above. The AI Advisor
  (Phase 6) explains these already-computed numbers in natural language;
  it does not compute or adjust them.
