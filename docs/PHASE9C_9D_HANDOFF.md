# Rapid Phase 9C / 9D handoff

Implemented directly on the supplied Phase 9B codebase:

- AI output now carries separate official risk and AI advisory severity, grounded evidence and a disagreement note. Model suggestions without one unambiguous matching file are discarded (conservative, potentially omits useful project-level advice).
- When the provider is unavailable or returns invalid JSON, the advisor returns deterministic metric-based recommendations. `available=false` correctly indicates these are NOT LLM-generated.
- Added read-only `GET /api/projects/{id}/quality-gate` with three illustrative rules and explicit `NOT EVALUATED` handling. The historical regression gate and user-editable thresholds are not yet implemented.
- Added `GET /api/projects/{id}/language-health` and a dashboard panel with per-language counts, LOC, average module complexity/MI and risk counts. Means are not calibrated across languages.
- Added evidence details to AI cards.

## Verification
Python compilation completed. The full pytest suite is blocked in this environment because `javalang` is not installed. Vite build is blocked because `node_modules` is not installed. No full suite or frontend build pass is claimed. The report/PDF has not been extended in this rapid handoff. Existing code is preserved.

## Next priority
Install requirements on the local machine, run `python -m pytest -q` inside `backend` and `npm ci && npm run build` inside `frontend`, fix failures, then verify the upload/version/AI/quality gate/PDF workflow end to end. Do not call the project final until that verification is done.
