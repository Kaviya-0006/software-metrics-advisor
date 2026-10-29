# Backend — Software Metrics Advisor API

FastAPI backend. Phase 1: app foundation, health check, SQLite connection.
Phase 2: project ZIP upload, source scanning, and the full metric-analysis
engine (Python, Java, C, C++, JavaScript and TypeScript; the last four were added afterwards through the same pipeline). Phase 3: metric normalization, Project Health
Score, and rule-based module risk detection, layered onto the same
upload response. Phase 4: read APIs for listing/inspecting analyzed
projects, plus database-quality and error-handling hardening. Phase 6:
an AI Advisor (Anthropic or local Ollama) that explains the
already-computed metrics/health/risk in plain language — it never
calculates them. Phase 7A: chronological scan history, grouped by
project name, for historical trend charts.

## Setup

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # or copy manually on Windows
```

## Run

```bash
uvicorn app.main:app --reload
```

API available at `http://localhost:8000`.
Interactive docs at `http://localhost:8000/docs`.

## Test

```bash
pytest
```

## Endpoints (Phase 1-7A)

| Method | Path                          | Purpose                                    |
|--------|-------------------------------|----------------------------------------------|
| GET    | `/`                           | Root — confirms API is up                  |
| GET    | `/api/health`                 | Health check                               |
| POST   | `/api/projects/upload`        | Upload a `.zip` project; returns raw metrics, Project Health Score, category scores, and ranked risky modules |
| GET    | `/api/projects`               | List every analyzed project, most recent first |
| GET    | `/api/projects/{id}`          | Full detail: health, category scores, summary, modules, risky-module ranking |
| GET    | `/api/projects/{id}/metrics`  | Module-level raw Phase 2 metrics only        |
| GET    | `/api/projects/{id}/risks`    | Risk-level counts + risky-module ranking      |
| GET    | `/api/projects/{id}/recommendations` | AI-generated explanations/recommendations; `available: false` if no AI provider is configured |
| GET    | `/api/projects/{id}/history`  | Chronological scan history for the logical project, grouped by exact project name |

`POST /api/projects/upload` accepts a `multipart/form-data` body with a
`file` field (the `.zip`). The `GET` endpoints under `/api/projects` are
pure reads — none of them recompute metrics, the health score, or risk
(including `/recommendations`, which only explains persisted data via an
LLM, and `/history`, which only re-groups already-persisted `Project`
rows); they read exactly what upload already persisted. `/{id}` routes
return `404` for an unknown project. See `docs/metrics.md` for what each
raw metric means, `docs/health-score.md` for the Health Score /
risk-score formulas and weights, `docs/ai-advisor.md` for the AI
Advisor, `docs/architecture.md` for the scan-history grouping approach
and its known limitation, and `app/api/upload.py` / `app/api/projects.py`
for the endpoints themselves.

Report endpoints (PDF, Project Health Certificate) are added in a later
phase.
