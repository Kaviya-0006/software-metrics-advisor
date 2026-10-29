# Project Plan

## Purpose

AI-Assisted Software Metrics Advisor with Intelligent Project Health
Monitoring: a system that scans an uploaded software project, calculates
software metrics (complexity, maintainability, coupling, cohesion,
duplication, testing, documentation, code churn), computes an overall
Project Health Score, identifies risky modules, and uses an LLM to explain
findings and recommend actions in plain language.

## Phases

1. **Foundation** — backend, frontend, database, and folder structure.
2. **Scanner & Metric Engine** — upload, parse Python/Java source, compute
   raw metrics.
3. **Quality Scoring & Risk Detection** — normalize metrics, compute
   Project Health Score, detect and rank risky modules.
4. **Backend API & Database Integration** — persist projects, modules,
   metric history, and recommendations; expose REST APIs.
5. **Frontend Dashboard** — upload UI, metric cards, risk table, charts,
   historical trends.
6. **AI Advisor** — structured-metric-to-recommendation pipeline via LLM.
7. **Final Features** — PDF report, Project Health Certificate, historical
   comparison, polish, documentation.

Optional, after Phase 7: ML-based future-risk prediction (scikit-learn).

## Current Status

**Phase 1 — Foundation: complete.**
**Phase 2 — Scanner & Metric Engine: complete.**
**Phase 3 — Quality Scoring & Risk Detection: complete.**
**Phase 4 — Backend API & Database Completion: complete.**
**Phase 5 — Frontend Dashboard & Project Visualization: complete.**
**Phase 6A — AI Advisor Backend Foundation: complete.**
**Phase 6B — Ollama Provider Integration: complete.**
**Phase 6C — Frontend AI Recommendation UI: complete.**
**Phase 6D — AI Advisor Final Acceptance: complete.**
**Phase 7A — Historical Trends & Project History: complete.**

Phase 1:
- FastAPI backend running with `/` and `/api/health` endpoints
- SQLite connected via SQLAlchemy; `Project` model initialized
- React (Vite) frontend with a placeholder dashboard
- Frontend confirms backend connectivity via the health endpoint

Phase 2:
- `POST /api/projects/upload` — safe ZIP upload, extraction (zip-slip /
  zip-bomb protected), and full analysis in one call
- Source scanner + Python (`ast`) and Java (`javalang`) parsers
- All 7 raw metrics implemented: LOC, Cyclomatic Complexity,
  Maintainability Index, Coupling, Cohesion (LCOM1), Depth of Inheritance,
  Code Duplication — see `docs/metrics.md` for full definitions, formulas,
  and documented limitations
- `Module` table persists per-file results alongside `Project`
- Minimal frontend upload panel wired to the real endpoint
- `sample_projects/demo_python` and `sample_projects/demo_java` for
  manual/automated verification (the Python sample includes a deliberate
  cross-file duplicate to exercise the duplication metric)

Phase 3:
- Rule-based metric normalization (0-100, higher = better) for every
  Phase 2 metric that supports it — see `docs/health-score.md` for every
  formula, threshold, and citation
- Category scores (Maintainability, Complexity, Code Quality, Testing,
  Coupling/Cohesion, Duplication, Documentation, Code Churn) and a
  weighted **Project Health Score** with Health Status
  (CRITICAL/POOR/NEEDS_ATTENTION/HEALTHY/EXCELLENT)
- Testing uses a static test-file presence score (not runtime coverage); Code
  Churn remains `null` until version-to-version change data is incorporated.
- Deterministic, rule-based **module Risk Score**, Risk Level
  (LOW/MEDIUM/HIGH/CRITICAL), named risk factors, and project-wide risky-
  module ranking — no LLM, no ML
- `POST /api/projects/upload` response extended with `health`,
  `category_scores`, and `risky_modules`; each module in `analysis.modules`
  also carries its own `risk_score`/`risk_level`/`risk_factors`
- `Project.health_score`/`health_status`/`category_scores` and
  `Module.risk_score`/`risk_level`/`risk_factors` persisted to SQLite
- 85 backend tests passing, including end-to-end runs of both sample
  projects through the full ZIP -> ... -> database pipeline
- No AI Advisor, ML, historical trends, final dashboard, or PDF reports
  yet — those are Phase 4 onward

Phase 4:
- New read APIs, all reusing persisted Phase 2/3 data (nothing
  recomputed): `GET /api/projects` (list), `GET /api/projects/{id}`
  (full detail), `GET /api/projects/{id}/metrics` (raw per-module
  metrics), `GET /api/projects/{id}/risks` (risk counts + ranking)
- `/{id}` routes return a clean `404` for a missing project; a
  malformed id returns FastAPI's standard `422`; a top-level exception
  handler keeps any other unexpected error from leaking a stack trace
  to the client (returns a generic `500`, logs server-side)
- `test_coverage` remains explicitly `null` in module metrics because uploaded
  source is never executed; the project-level Testing category uses the static
  test-file presence signal. `documentation_percentage` is a transparently-labeled
  derived value from already-stored comment/code line counts, not a
  new fabricated metric
- Database-quality verified directly against the real models: Project
  -> Module foreign key, cascade delete (deleting a Project removes its
  Module rows), JSON field round-tripping, null metric persistence, and
  fresh-database table creation
- Scan history: no new table added — re-uploading a project creates a
  new `Project` row rather than overwriting one, so history is already
  preserved without extra schema; see `docs/architecture.md` for the
  full reasoning
- `/docs` and `/openapi.json` verified working with descriptions/response
  models for every endpoint
- 106 backend tests passing (up from 85 — 16 new API tests, 5 new
  database-quality tests)
- No AI Advisor, ML, historical trends *feature*, final dashboard, or
  PDF reports yet — those are Phase 5 onward

Phase 5:
- Full React dashboard: project list/switching, health gauge, summary
  cards, category-score chart, risk distribution, risky-module table,
  module detail panel — all hand-rolled SVG/CSS, no charting library
- Light professional theme; every value sourced from the Phase 4 read
  APIs, no client-side metric/health/risk calculation anywhere
- Upload flow wired end-to-end: select ZIP -> analyze -> new project
  auto-selected -> its dashboard loads
- Null vs. zero handling verified repeatedly against real data
  (`documentation: 0.0` stays `0`, `testing`/`code_churn`/`test_coverage`
  stay `N/A`)
- Keyboard accessibility, focus states, retry controls, responsive layout
  (desktop/tablet/mobile) added in later Phase 5 sub-passes
- No new frontend dependencies at any point — still just `react` +
  `react-dom` + Vite tooling
- Final Phase 5 acceptance pass confirmed all of the above plus the full
  API contract against the live backend

Phase 6A:
- AI Advisor backend foundation: `app/ai/advisor.py` (context building +
  provider call + response normalization) and `app/ai/prompts.py` (system
  prompt) — see `docs/ai-advisor.md` for the full data flow
- `GET /api/projects/{id}/recommendations` — reads only persisted
  Project/Module data (reusing the same helpers `/metrics` and `/risks`
  already use), builds a structured context, calls the configured
  provider, normalizes the result; never recalculates metrics, health
  score, or risk
- AI is never in the path that produces health/risk/metric values —
  verified by a test asserting a project's persisted data is identical
  before and after a recommendations call
- Provider configuration via `AI_PROVIDER`/`AI_MODEL`/`AI_API_KEY`/
  `AI_REQUEST_TIMEOUT_SECONDS`; defaults to disabled (`AI_PROVIDER=none`)
  so the rest of the app is unaffected when AI isn't configured
- Clean `available: false` responses (never fabricated advice) for: not
  configured, provider network/timeout/HTTP failure, and malformed
  provider output; partially-malformed output keeps valid entries
- No secrets ever appear in a response — verified by test
- 130 backend tests passing (up from 106 — 18 new AI-advisor unit tests,
  6 new recommendations-API tests)
- No frontend changes in Phase 6A; AI recommendation UI is a later phase

Phase 6B-6D (Ollama provider, frontend recommendation UI, final
acceptance, and two real-world Ollama JSON-shape fixes discovered after
live testing on the user's own machine):
- Second AI provider, `ollama` (local, no API key required — distinct
  from `anthropic`'s required key, both correctly gated in
  `is_ai_configured()`)
- `AIRecommendations.jsx` + `getProjectRecommendations()` display backend
  recommendations with loading/unavailable/empty/error states; project
  switching and upload both correctly avoid stale AI data, following the
  same defensive effect pattern as the rest of the dashboard
- Real qwen2.5:3b output on the user's Windows machine revealed two
  response shapes beyond the originally-expected bare array — a
  `{"recommendations": [...]}` wrapper and a single bare recommendation
  object — both now accepted by `_extract_recommendation_list()`
  (schema-location, not schema-relaxation: every entry still goes
  through identical per-field validation either way)
- Prompt strengthened to explicitly distinguish `0` (a real measured
  value) from `null` (unavailable), with per-metric examples
- 171 backend tests passing

Phase 7A:
- `GET /api/projects/{id}/history` — chronological scan history for the
  logical project, grouped by exact project name (every upload already
  creates a new `Project` row rather than overwriting one — no schema
  change needed); returns `health_score`/`health_status`/
  `category_scores` per scan, no module-level data
- `HistoryTrends.jsx` — hand-rolled SVG line charts for the Health Score
  trend and a switchable category-score trend, reusing
  `CategoryScoresChart.jsx`'s label/order constants; a `null` historical
  value breaks the line rather than plotting as 0; single-scan projects
  show an explanatory message instead of a fabricated trend
- Known, documented limitation: grouping by exact project name means two
  unrelated projects uploaded with the same filename would appear as one
  history line — see `docs/architecture.md`'s "Scan history" section
- 181 backend tests passing (171 + 10 new history tests)
