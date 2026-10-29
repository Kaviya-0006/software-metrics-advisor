# Frontend — Software Metrics Advisor UI

React (Vite) frontend. Phase 1: placeholder dashboard + backend
connectivity check. Phase 2: project upload. Phase 5: the full dashboard
(health gauge, summary cards, category scores, risk distribution,
risky-module table, module detail). Phase 6C: AI recommendations.
Phase 7A: historical trend charts. All charts are hand-rolled SVG — no
charting library is used anywhere in this project.

## Setup

```bash
cd frontend
npm install
```

## Run

```bash
npm run dev
```

Opens at `http://localhost:5173`. Requires the backend running at
`http://localhost:8000` (see backend/README.md) for the "Backend Status"
indicator to show "Connected" and for uploads/dashboard data to work.

## Structure

- `src/pages/Dashboard.jsx` — owns the project list + selected-project
  data flow (health/risk detail, AI recommendations, scan history), all
  via independent, stale-response-safe fetch effects
- `src/components/UploadPanel.jsx` — select a `.zip`, upload, auto-selects
  the newly analyzed project on success
- `src/components/HealthGauge.jsx` — circular SVG gauge for the Health Score
- `src/components/SummaryCards.jsx` — health/files/classes/functions/LOC/
  risky-module-count stat cards
- `src/components/CategoryScoresChart.jsx` — horizontal bar chart for the
  8 Phase 3 categories; exports `CATEGORY_LABELS`/`CATEGORY_ORDER`, reused
  by `HistoryTrends.jsx` so the two views never disagree on category names
- `src/components/RiskOverview.jsx` — LOW/MEDIUM/HIGH/CRITICAL bar chart
- `src/components/RiskyModulesTable.jsx` — sortable risk table; also
  exports the shared `RiskLevelPill` used by `AIRecommendations.jsx`
- `src/components/ModuleDetailPanel.jsx` — full metric + risk detail for
  one selected module
- `src/components/AIRecommendations.jsx` — displays backend AI
  recommendations with loading/unavailable/empty/error states
- `src/components/ArchitectureIntelligence.jsx` + `src/utils/architectureDiagram.js` — Phase 8D Architecture Intelligence section (layers, diagram, API matches, potential issues, unused endpoints, uncertain findings with file:line evidence) and its SVG export; `npm test` runs the diagram renderer tests.
- `src/utils/trendData.js` — Phase 9A pure trend-data preparation (V1/V2/V3 series, metric definitions, change/direction logic); `HistoryTrends.jsx` renders it with a metric selector.
- `src/components/VersionComparison.jsx` + `src/utils/comparisonView.js` — Phase 9B Version Comparison section (selectors, before/after cards, regression alerts, improvements, filterable file table, architecture diff); tests use a fixture generated from the real backend comparison engine.
- `src/components/HistoryTrends.jsx` — SVG line charts for the Health
  Score trend and a switchable category-score trend across scans; shows
  an explanatory message instead of a chart when only one scan exists
- `src/services/api.js` — every backend API call, one function per
  endpoint, consistent error handling throughout

Null vs. zero handling is treated as a first-class concern throughout:
every component uses explicit `!= null`/`??` checks, never truthiness, so
a genuine `0` (zero coupling, zero duplication, a `documentation_percentage`
of `0.0`) is never rendered as "N/A", and an unavailable (`null`) value is
never rendered as `0`.
