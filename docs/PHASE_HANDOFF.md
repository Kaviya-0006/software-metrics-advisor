# Phase handoff (state after Phase 9B)

Use this file to start the next phase in a fresh chat.

## Completed
Phases 1-7, 8A-8C (architecture / endpoint / call / matching analyzers), 8D
(Architecture Intelligence integration), 9A (project identity, immutable version
history, working trends), 9B (version comparison and file-level regression
intelligence). Phase 9C has **not** been started.

## Key concepts
* `projects` row = one upload = one **scan snapshot**. `project_id` in every
  `/api/projects/{project_id}/...` route is that scan id.
* `project_identities` = the stable project; `project_versions` = immutable
  per-upload version row (`scan_id` unique, `(identity_id, version_number)` unique).
* Upload placement: explicit `identity_id`, `create_new`, or automatic (name AND
  structure, see docs/architecture.md "Project identity and version history").
* `architecture_scans` = one Architecture Intelligence report per scan.
* `file_fingerprints` = per-file SHA-256 captured at upload (Phase 9B); older scans have none.
* Comparison: `GET /api/identities/{id}/compare`; engine = `backend/app/analyzer/comparison.py`
  (pure, thresholds in `PROJECT_RULES` / `FILE_RULES`, published in each response).
* The frontend picker lists identities (`GET /api/identities`); versions are
  selected with the version selector; trends come from
  `GET /api/projects/{id}/history` through `frontend/src/utils/trendData.js`.

## Where things live
* Backend: `backend/app/analyzer/{identity,architecture_report,comparison}.py`,
  `backend/app/database/{models,versioning}.py`, `backend/app/api/{upload,projects,identities}.py`.
* Frontend: `frontend/src/components/{HistoryTrends,UploadPanel,ArchitectureIntelligence}.jsx`,
  `frontend/src/components/VersionComparison.jsx`,
  `frontend/src/utils/{trendData,architectureDiagram,comparisonView}.js`, `frontend/src/pages/Dashboard.jsx`.
* Tests: `backend/tests/` (pytest; Phase 9A: `test_project_identity.py`,
  `test_project_versions.py`; Phase 9B: `comparison_fixtures.py`, `test_comparison_engine.py`,
  `test_version_comparison_api.py`), `frontend/tests/*.test.mjs` (`npm test`, Node's runner).

## Verify on a machine with network access
```
cd backend && pip install -r requirements.txt && pytest
cd ../frontend && npm install && npm test && npm run build
```

## Known limitations carried forward
* No endpoint to delete / rename / move a version or project.
* "Unchanged" files cannot be proven for scans stored before Phase 9B (reported `unverified`).
* File renames are only suggested (with confidence), never confirmed.
* Identity matching uses file names and source-file paths only.
* Versions backfilled from pre-9A scans have NULL project-level duplication %.
* Architecture results are not part of the PDF report or the AI Advisor context.
* Findings from the architecture analyzers are static-analysis hints.
