# AI-Assisted Software Metrics Advisor with Intelligent Project Health Monitoring

Uploads a software project, calculates software metrics, computes a
Project Health Score, identifies risky modules, and uses AI to explain
findings and recommend actions. See `docs/project-plan.md` for the full
phase plan and `docs/architecture.md` for the pipeline architecture.

**Current status: Phase 7A (Historical Trends) complete.**

## Quickstart

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
copy .env.example .env       # Windows
# cp .env.example .env       # macOS / Linux
uvicorn app.main:app --reload
```

Backend runs at `http://localhost:8000` (`/docs` for interactive API docs).

### Frontend

```bash
cd frontend
npm install
copy .env.example .env       # Windows, optional — defaults work as-is
npm run dev
```

Frontend runs at `http://localhost:5173` and should show
**Backend Status: Connected** once the backend is running. Upload a `.zip`
of a Python, Java, C, C++, JavaScript or TypeScript project (e.g. zip up `sample_projects/demo_python/`)
to see it scanned, scored, and risk-ranked.

Supported languages:

- Python (`.py`)
- Java (`.java`)
- C (`.c`, and `.h` headers that use only C syntax)
- C++ (`.cpp`, `.cc`, `.cxx`, `.hpp`, and `.h` headers that use C++ syntax)
- JavaScript (`.js`, `.jsx`)
- TypeScript (`.ts`, `.tsx`, including `.d.ts` declaration files)

Not scanned: `.mjs` and `.cjs` files. Minified bundles (`*.min.js`) and
binary `.ts` files (MPEG transport-stream video that happens to share the
extension) are skipped. Common build/dependency directories (`node_modules`,
`dist`, `build`, `bin`, `out`, ...) are ignored for every language. See
`docs/metrics.md` (sections 9 and 10) for how C/C++ and JavaScript/TypeScript
are parsed and what their known limitations are.

### Tests

```bash
cd backend
pytest

# frontend diagram renderer (Phase 8D), Node's built-in test runner
cd ../frontend
npm test
```

## Project structure

```
software-metrics-advisor/
├── backend/          FastAPI app, analyzer engine, database layer, tests
├── frontend/         React (Vite) app
├── sample_projects/  small demo projects for testing the analyzer: demo_python,
│                     demo_java, demo_c, demo_cpp, demo_javascript, demo_typescript
│                     (zip any one of them and upload it)
├── uploads/          uploaded project archives (extracted here temporarily)
├── reports/          generated PDF reports (Phase 7)
├── docs/             project plan, architecture, and full metrics reference
└── README.md
```

## API (Phase 1-9B)

| Method | Path                          | Purpose                                   |
|--------|-------------------------------|--------------------------------------------|
| GET    | `/`                           | Root — confirms API is up                 |
| GET    | `/api/health`                 | Health check                              |
| POST   | `/api/projects/upload`        | Upload a project ZIP; returns raw metrics, Project Health Score, category scores, and ranked risky modules |
| GET    | `/api/projects`               | List every analyzed project                |
| GET    | `/api/projects/{id}`          | Full detail for one project                |
| GET    | `/api/projects/{id}/metrics`  | Module-level raw metrics for one project    |
| GET    | `/api/projects/{id}/risks`    | Risk-level counts + risky-module ranking    |
| GET    | `/api/projects/{id}/recommendations` | AI-generated explanations/recommendations — `available: false` if no AI provider is configured |
| GET    | `/api/projects/{id}/history`  | Phase 9A: immutable version history (V1, V2, ...) of the project this scan belongs to, with stored health/complexity/maintainability/duplication/risk/LOC/language metrics |
| GET    | `/api/identities` | Phase 9A: projects (stable identities) with version count and latest scan id |
| GET    | `/api/identities/{identity_id}` | Phase 9A: one project with all of its versions |
| GET    | `/api/identities/{identity_id}/compare` | Phase 9B: compare two versions (default: latest vs predecessor; `from`/`to` version numbers) - metric cards, per-file changes, regressions/improvements, architecture diff, uncertainty |
| POST   | `/api/projects/suggest-match` | Phase 9A: which existing projects a ZIP may be a new version of (nothing saved) |
| GET    | `/api/projects/{id}/architecture` | Phase 8D: Architecture Intelligence saved for this scan (layers, diagram graph, matched/unmatched API calls, unused endpoints, uncertain findings, file:line evidence) — `available: false` for scans made before 8D |

See `docs/metrics.md` for exactly how every raw metric is calculated,
`docs/health-score.md` for the Health Score / risk-detection formulas and
weights, `docs/ai-advisor.md` for what the AI Advisor does (and does not
do), and `docs/architecture.md` for the full pipeline, API surface, and
project identity / version history (Phase 9A) and the earlier scan-history design.

## Roadmap

Phase 7B (PDF reports, Project Health Certificate) onward — see
`docs/project-plan.md`.
