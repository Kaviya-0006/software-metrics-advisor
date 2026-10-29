# AI Advisor (Phase 6A)

## Purpose

The AI Advisor explains a project's **already-computed** Phase 2 metrics,
Phase 3 Health Score, and Phase 3 risk results in plain language, and
suggests practical improvements. It is an explanation and prioritization
layer on top of the deterministic pipeline — **it does not compute
anything**.

```
Source Code
  -> Scanner (Phase 2)
  -> Metrics Engine (Phase 2)
  -> Health Score (Phase 3)
  -> Risk Detection (Phase 3)
  -> Structured Project/Module Data  <-- persisted in SQLite, unchanged
  -> AI Advisor (Phase 6A)           <-- reads that data, explains it
  -> Explanations + Recommendations
```

The metrics, Health Score, category scores, and risk scores remain the
**sole source of truth**. The AI Advisor is never in the path that
produces them, and calling it can never change a persisted value — see
`tests/test_recommendations_api.py::test_recommendations_does_not_change_persisted_health_or_risk`,
which asserts a project's health/category/module/risk data is bit-for-bit
identical before and after a recommendations call.

## Endpoint

```
GET /api/projects/{project_id}/recommendations
```

Read-only, like every other `/api/projects/{id}/...` endpoint (see
`docs/architecture.md`). It loads the persisted `Project` and `Module`
rows, builds a structured context from them, and calls the AI Advisor —
it never re-scans the upload, never re-runs the metric engine, and never
re-runs the health-score or risk algorithms.

## What is sent to the AI

A single JSON object, built by `app.ai.advisor.build_ai_context()`:

```json
{
  "project": {
    "name": "...",
    "health_score": 79.58,
    "health_status": "HEALTHY",
    "category_scores": { "maintainability": 72.0, "testing": null, "...": "..." },
    "summary": { "files": 3, "classes": 7, "functions": 21, "total_loc": 350 }
  },
  "modules": [
    {
      "file_name": "...", "language": "...",
      "loc": { "total": 100, "code": 80, "comments": 10, "blank": 10 },
      "classes": 1, "functions": 5,
      "complexity": { "total": 10, "average": 2.0, "maximum": 4 },
      "maintainability_index": 70.0, "coupling": 3, "cohesion": 0.0,
      "depth_of_inheritance": 1, "duplication_percentage": 5.0,
      "test_coverage": null, "documentation_percentage": 12.5,
      "risk_score": 22.4, "risk_level": "LOW", "risk_factors": []
    }
  ]
}
```

Every field is read straight from the database (via the same
`_module_metrics_dict` / `_module_risk_dict` helpers `app/api/projects.py`
already uses for the metrics/risks endpoints) — nothing here is
recalculated for the AI's benefit. `null` metrics (e.g. `test_coverage`)
are sent as `null`, and genuine zero values (e.g. `coupling: 0`) are sent
as `0` — the two are never confused.

## What is NOT sent

- **No source code.** The AI never sees the uploaded project's file
  contents — only the numeric/textual metrics and risk data above.
- **No file paths beyond the file name** needed for identification.
- **No unrelated project data** (other projects, other users' data).

## System prompt

`app/ai/prompts.py` instructs the model explicitly that:
- the supplied metrics/scores are authoritative and must never be
  recalculated, second-guessed, or overridden;
- missing (`null`) values must be treated as genuinely unavailable, never
  guessed;
- `null` and `0` are never the same thing — `null` means "not measured,"
  `0` means "measured, and the value is genuinely zero" (e.g.
  `"test_coverage": 0` is a real, flaggable zero-coverage result,
  completely different from `"test_coverage": null`, which means no
  coverage data exists at all);
- it must distinguish observed facts from its own recommendations;
- recommendations must be specific, evidence-grounded, and prioritized
  (lower `priority` number = more important; HIGH/CRITICAL risk modules
  before LOW/MEDIUM ones);
- it must not generate or rewrite source code, and must not claim a fix
  has already been applied.

## Output format

```json
{
  "project_id": 1,
  "available": true,
  "recommendations": [
    {
      "problem": "...",
      "severity": "LOW | MEDIUM | HIGH | CRITICAL",
      "explanation": "...",
      "recommendation": "...",
      "priority": 1
    }
  ],
  "message": null
}
```

When the AI is not configured or fails:

```json
{
  "project_id": 1,
  "available": false,
  "recommendations": [],
  "message": "AI advisor is not configured."
}
```

`available: false` is a normal, expected response shape — not an HTTP
error. `GET /api/projects/{id}/recommendations` returns `200` in every
one of these cases; only an invalid `project_id` returns `404`.

## Provider configuration

Two providers are supported. Pick one via `AI_PROVIDER`; the other's
variables are simply ignored.

### Option A: Anthropic (hosted — needs an API key)

| Variable | Default | Purpose |
|---|---|---|
| `AI_PROVIDER` | `none` | Set to `anthropic` to enable this provider. |
| `AI_MODEL` | `claude-haiku-4-5-20251001` | Model identifier — not a secret. |
| `AI_API_KEY` | *(empty)* | **Required** for this provider. Falls back to `LLM_API_KEY` if set, for continuity with the Phase 1 placeholder. Never hard-coded, never logged, never included in any error message returned to the client. |
| `AI_REQUEST_TIMEOUT_SECONDS` | `30` | How long to wait before treating the provider as failed. |

### Option B: Ollama (local — no API key needed)

Ollama is a locally-run, unauthenticated model server. Unlike Anthropic,
**it does not require an API key** — `is_ai_configured()` treats
`AI_PROVIDER=ollama` as configured as soon as `OLLAMA_BASE_URL` is set
(which it always is, via its default), with no `AI_API_KEY` check at all
for this provider.

| Variable | Default | Purpose |
|---|---|---|
| `AI_PROVIDER` | `none` | Set to `ollama` to enable this provider. |
| `AI_MODEL` | `claude-haiku-4-5-20251001` | Set this to whatever model you have pulled locally, e.g. `qwen2.5:3b`. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Base URL of your running Ollama server. |
| `AI_REQUEST_TIMEOUT_SECONDS` | `30` | How long to wait before treating the provider as failed — local models can be slower than a hosted API, so raise this if needed. |

**Setup is entirely your responsibility, outside this application:**
this project never installs Ollama and never downloads a model for you.
Before setting `AI_PROVIDER=ollama`, you must have:

1. [Installed Ollama](https://ollama.com) separately and have it running
   (`ollama serve`, or the desktop app).
2. Pulled the model you intend to use, e.g. `ollama pull qwen2.5:3b`.
3. Confirmed it's reachable, e.g. `curl http://localhost:11434/api/tags`.

The request itself uses Ollama's `/api/chat` endpoint with
`"format": "json"` (Ollama's basic JSON-mode, which constrains the model
to emit syntactically valid JSON). This is a best-effort guarantee from
the model, not a schema validator, so the same normalization/validation
layer described below remains the actual safety boundary regardless of
which provider is used.

## Unavailable-provider behavior

If `AI_PROVIDER` is `none`, the selected provider's requirements aren't
met (no `AI_API_KEY` for Anthropic; unreachable `OLLAMA_BASE_URL` for
Ollama — e.g. Ollama isn't running), the provider request fails (network
error, timeout, non-2xx HTTP status), or the provider's response can't be
safely parsed as the expected JSON array, the endpoint returns a clean
`available: false` response with a short, generic `message` — never a
fabricated recommendation, never a raw exception message, and never the
API key, the configured URL, or any other provider-internal detail. The
rest of the application (upload, metrics, health score, risk detection,
every other dashboard endpoint) is completely unaffected either way.

A partially-malformed AI response (a JSON array where some entries are
missing required fields) keeps the valid entries and silently drops the
invalid ones, rather than discarding the whole response.

## Limitations

- Recommendations are **advisory**, not guaranteed fixes — a developer
  should evaluate each suggestion, not apply it blindly.
- The AI Advisor has no way to verify a recommendation actually resolves
  the underlying issue; it reasons only from the supplied metrics.
- Severity/priority ordering reflects the AI's judgment, not a
  deterministic formula (unlike the Health Score and Risk Score, which
  are entirely rule-based — see `docs/health-score.md`).
- Two providers are supported: `anthropic` (hosted, needs an API key) and
  `ollama` (local, no API key, but you must install/run it and pull a
  model yourself — this project never does that for you). Both are
  reachable with the `httpx` dependency this project already has, so no
  new package was needed for either.
