# Architecture

```
User
 |
 v
Project Upload
 |
 v
Project Scanner
 |
 v
Source Code Parser
 |
 v
Metric Engine
 |
 v
Quality Analysis
 |
 v
Health Score
 |
 v
Risk Detection
 |
 v
AI Advisor
 |
 v
Dashboard
 |
 v
Reports
```

## Stack

## Stack

- **Frontend:** React (Vite), JavaScript, Chart.js (added in Phase 5)
- **Backend:** Python, FastAPI
- **Database:** SQLite (via SQLAlchemy)
- **Analysis:** Python `ast` + `radon` (Python source), `javalang` (Java
  source), and a dependency-free lexer + structure scanner for C/C++
  (`analyzer/cfamily.py`), and a second dependency-free lexer + structure
  scanner for JavaScript/TypeScript (`analyzer/jsfamily.py`) — see
  `docs/metrics.md` for exactly how each metric is derived

  The C/C++ and JavaScript/TypeScript front ends are *static
  approximations*, not compilers. Each one only replaces the parser step:
  it turns a source file into the same `ParsedModule` / `ParsedClass` /
  `ParsedMethod` structures that Python and Java produce, and from there
  the language-agnostic parts of the pipeline are shared unchanged — the
  per-metric calculators, the normalization and Project Health Score, the
  module risk rules, the database, the API, the AI Advisor input, the PDF
  report, and the dashboard/SVG export. There is deliberately no
  per-language scoring system, no per-language API route and no
  per-language dashboard; the frontend contains no language-specific logic.
- **AI:** LLM API, given structured metric JSON — not raw source code
- **Reports:** Python PDF generation library (added in Phase 7)

## API surface (Phase 4)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Root health check |
| GET | `/api/health` | Service health check |
| POST | `/api/projects/upload` | Upload + fully analyze a project ZIP (scan, metrics, health score, risk) |
| GET | `/api/projects` | List every analyzed project, most recent first |
| GET | `/api/projects/{id}` | Full detail: health, category scores, summary, modules, risky-module ranking |
| GET | `/api/projects/{id}/metrics` | Module-level raw Phase 2 metrics only |
| GET | `/api/projects/{id}/risks` | Risk-level counts + risky-module ranking |
| GET | `/api/projects/{id}/recommendations` | AI-generated explanations/recommendations (Phase 6A) — reads persisted data only, `available: false` if AI isn't configured |
| GET | `/api/projects/{id}/history` | Chronological scan history for the logical project (Phase 7A) — grouped by exact project name, see "Scan history" below |

All four `GET` endpoints are pure reads against what `POST
/api/projects/upload` already computed and persisted — none of them
re-run the scanner, the metric engine, the health-score normalization, or
the risk algorithm. `/{id}` routes return `404` with a plain `{"detail":
...}` body (no stack trace) when the project doesn't exist; a malformed
`{id}` (non-integer) returns FastAPI's standard `422` validation error,
also with no internal detail leaked. A top-level exception handler in
`app/main.py` catches anything else unexpected and returns a generic
`500` — the real exception is logged server-side only.

## Scan history — design decision (Phase 4, implemented Phase 7A)

Phase 4 asked us to make sure re-analyzing "the same project" doesn't
destroy previous scan results, without building the full historical-
trends feature yet. The existing schema already satisfied this without
any new table: **every upload creates a new `Project` row** (there is no
upsert-by-name), so uploading `demo_python.zip` twice produces two
independent `Project` records, each with its own `upload_date`, health
score, and full set of `Module` rows — the first scan's results are
never overwritten or deleted. `GET /api/projects` already lists all of
them, most recent first.

Phase 7A built directly on that groundwork — no schema change was
needed. `GET /api/projects/{project_id}/history` groups every `Project`
row that shares the given project's exact `name`, orders them
chronologically by `upload_date`, and returns each scan's
`health_score`, `health_status`, and `category_scores` (no module-level
data — that stays specific to one scan via the existing `/{id}`,
`/{id}/metrics`, and `/{id}/risks` endpoints). It's a pure read: nothing
is rescanned, recomputed, or mutated.

**Known limitation, by design:** grouping is by exact project name only
— the name derived from the uploaded ZIP's filename (see
`app/api/upload.py`'s `_project_name_from_filename`). Two genuinely
unrelated projects uploaded with the same filename (e.g. both zipped as
`project.zip`) would appear as one history line. We did not build a more
robust "logical project identity" (a separate `project_group` table, a
UUID assigned at first upload, etc.) because the schema offers no better
stable identifier today, and inventing one would be speculative schema
design ahead of a real need — consistent with the project's "don't
over-build" principle (see the master specification, §5). If this
becomes a real problem, the smallest fix is letting the user optionally
name/rename a project explicitly at upload time, rather than inferring
identity from a filename.

See `frontend/src/components/HistoryTrends.jsx` for how this is
visualized: a hand-rolled SVG line chart for the Health Score trend, plus
a category-trend chart the user can switch between the 8 Phase 3
categories for, both reusing `CategoryScoresChart.jsx`'s label/order
constants so the two views can never disagree on category names. A
`null` historical category score breaks the line rather than being
plotted as 0; a genuine `0` plots normally. Projects with only one scan
show an explanatory message instead of a fabricated trend.

## Project Architecture Intelligence (Phase 8A — foundation)

A real upload is usually a *full-stack* system (for example a TypeScript
frontend and a Java backend), not a single-language project.
`backend/app/analyzer/architecture.py` turns the list of analyzed files into
an architecture view: which layers the project appears to have, the languages
and size of each layer, and how much confidence the evidence deserves.

**Scope of this phase.** Layer classification only. API-endpoint detection
(8B), frontend-to-backend matching (8C) and any UI (8D) are later phases. The
module is not wired into the upload endpoint, the database, the API
response or the dashboard: `ProjectAnalysis.to_dict()` is unchanged, and every
existing project analyzes exactly as before.

**Reuses the existing pipeline — no second scanner.** Languages are never
detected here. The input is what the pipeline already produces —
`ModuleResult` objects (`analysis.modules`), their stored `to_dict()` form, or
the scanner's `SourceFile` records — each carrying the language chosen by
`scanner.LANGUAGE_BY_EXT`. The module only looks at *where* a file lives and
*what it is called*. Layer LOC uses the same definition as the project total
(`loc.total`), so layer totals reconcile with `ProjectAnalysis.total_loc`.

```python
analysis = analyze_project(root)
summary = analyze_architecture(analysis.modules)
summary.to_dict()
# {"layers": {"frontend": {"languages": {"typescript": 12}, "files": 12,
#                          "loc": 1850, "confidence": "high"},
#             "backend":  {"languages": {"java": 8}, "files": 8, "loc": 1420,
#                          "confidence": "high"}},
#  "detected_layers": ["frontend", "backend"], "total_files": 20,
#  "total_loc": 3270, "languages": {...}, "confidence": "high"}
```

`ArchitectureSummary` exposes `detected_layers`, `languages_by_layer`,
`files_by_layer`, `total_files`, `total_loc`, `confidence`, `is_multi_layer`,
`layer_of(path)`, and (optionally, `to_dict(include_files=True)`) the
per-file classification with its evidence.

**Layers.** `frontend`, `backend`, `api`, `database`, `shared`, `tests`,
`configuration`, `unknown`. Every file is assigned to exactly one layer, so
`files_by_layer` always sums to `total_files`; files without evidence are
reported under `unknown` (never guessed) and are not counted as a detected
layer. `api` is a backend-side layer, reported separately because it is the
input to the later API-surface analysis.

**Evidence, strongest first.**

| Evidence | Examples | Confidence of a file |
| --- | --- | --- |
| Strong directory name (whole path segment, case-insensitive) | frontend: `frontend`, `front-end`, `client`; backend: `backend`, `back-end`, `server`; tests: `test`, `tests`, `__tests__`, `spec`, `specs`, `e2e`; database: `db`, `database`, `migrations`; shared: `shared`, `common`; configuration: `config`, `configs`, `configuration`, `conf`, `settings` | HIGH; MEDIUM if the language does not fit (for example Python inside `frontend/`) |
| Weak (ambiguous) directory name | `web`, `webapp`, `ui`, `app` (frontend); `services` (backend); `api`, `routes`, `controllers` (api); `repositories`, `dao` (database); `lib`, `utils`, `helpers` (shared) | At most MEDIUM, and only when the language fits: `app/main.py` is *not* a frontend; JS/TS in `services/` or `api/` stays LOW because it may be client code |
| File-name convention | `*.test.ts`, `*.spec.ts`, `test_*.py`, `*_test.py`, `*Test.java`; `*.config.js`, `.eslintrc.js`, `settings.py`; `*Controller.java`; `*Repository.java`; `*.tsx`/`*.jsx` | LOW on its own |

**Precedence.** A strong test directory wins over everything (tests live
inside frontend and backend trees); a test *file name* also wins but drops to
MEDIUM when a directory suggests another layer. Otherwise the innermost strong
directory decides (`backend/config/x.py` is configuration), strong evidence
beats weak (`frontend/src/utils/x.ts` is frontend), and a side directory beats
`api` (`frontend/src/api/client.ts` is an API *client*, i.e. frontend). A path
containing both a frontend and a backend directory is contradictory and is
capped at MEDIUM. Weak directories and file names are only consulted when no
strong evidence exists.

**Confidence aggregation.** File levels score HIGH=3, MEDIUM=2, LOW=1. A
*layer* is HIGH at a mean of at least 2.6, MEDIUM at 1.8 or more, otherwise
LOW. The *project* additionally requires coverage — the share of files that
could be classified at all: HIGH needs a mean of at least 2.6 and 75%
coverage; MEDIUM needs 1.8 and 40%; otherwise LOW (including when nothing
could be classified). A well-structured project whose files mostly sit in
unconventional folders therefore reports a lower confidence rather than an
overstated one.

**Known limitations (Phase 8A).** Classification is path- and name-based: a
project that keeps frontend and backend in unconventional folders is reported
as `unknown`, and file contents are not inspected (that is the job of 8B/8C).
Only the languages the scanner supports are seen, so HTML/CSS/SQL files do
not appear in any layer. The vocabulary is English and convention-based
(`frontend`, `server`, ...); a project root folder with a layer-like name
(for example an upload whose top folder is `backend/`) is treated as that
layer. Weak names such as `app` are deliberately never trusted on their own.

## API endpoint detection (Phase 8B)

`backend/app/analyzer/api_endpoints.py` finds the HTTP endpoints a project
*declares*, using **static analysis only**: the uploaded code is never
executed, imported or contacted. Like Phase 8A it is additive and not yet
exposed through the upload endpoint, the database or the API response.

**Supported frameworks and patterns**

| Language | Framework | Patterns |
| --- | --- | --- |
| Python | FastAPI | `@app.get/post/put/patch/delete("/p")`, `@router.get(...)`, `@app.api_route("/p", methods=[...])`; a literal `APIRouter(prefix="/x")` is applied |
| Python | Flask | `@app.route("/p")`, `@bp.route("/p", methods=[...])` (no `methods` means GET), the Flask 2 shortcuts `@app.get(...)`; a literal `Blueprint(..., url_prefix="/x")` is applied |
| Java | Spring | `@GetMapping/@PostMapping/@PutMapping/@PatchMapping/@DeleteMapping`, `@RequestMapping(value=..., method=RequestMethod.X)`; the class-level `@RequestMapping("/api")` is joined to the method path (`/api` + `/users` = `/api/users`); path arrays and `path=`/`value=` forms |
| JavaScript, TypeScript | Express | `app.get/post/put/patch/delete("/p", handler)` and the same on `express()`, `express.Router()` or `Router()` receivers, including `this.router.get(...)`; literal paths, static template literals and arrays of literals |

Each `ApiEndpoint` records `method` (or `ANY`), `path`, `source_file`,
`line_number`, `language`, `framework`, `confidence`, and — when known — the
`handler` and the Phase 8A `layer` of its file. `ApiEndpointSummary` reports
`total_endpoints`, `by_method`, `by_framework`, `by_confidence`, and how many
files were searched, excluded by layer, and how many route declarations were
skipped because their path was not a literal.

**Source access.** The detector consumes the pipeline's own `ParsedModule`
list — language, source text, imports, parse error and parse tree (Python
`ast`, javalang's tree, the JS/TS lexer's token stream). There is no second
scanner, no second source loader and no regular expression over raw text, so a
route-looking comment, string, docstring or test description cannot match.
The engine's one new, opt-in switch is `analyze_project(root,
retain_parsed=True)`, which keeps the parsed modules on
`ProjectAnalysis.parsed_modules` (empty by default, never serialized, excluded
from equality). It exists because the upload flow deletes the extracted files
as soon as a request ends: a later analysis cannot re-read them, so it must
reuse the single parse the engine already performs. Default behaviour and every
serialized output are unchanged.

**Phase 8A integration.** When an `ArchitectureSummary` is supplied, files in
the frontend, tests, database, shared and configuration layers are not
searched; backend, api and *unknown* files are. An endpoint found in an unknown
file records `layer = "unknown"` — it is never relabelled backend. Without an
architecture summary every supported file is searched.

**Confidence.** HIGH: a clear framework-specific pattern whose framework is
established (the receiver is built by `FastAPI()`/`APIRouter()`/`Flask()`/
`Blueprint()`/`express()`/`Router()`, or the file imports the framework), or a
Spring mapping with an explicit HTTP method. MEDIUM: recognizable but
something is assumed — `@app.get` with no framework evidence (FastAPI and
Flask 2 share it), a Spring `@RequestMapping` without `method` (reported as
`ANY`), a non-literal router prefix, a Spring interface, an Express receiver
only known by name in a file that imports `express`. LOW: only the
conventional name `app`/`router` links an Express-shaped call to Express.

**Conservative by design.** Non-literal paths are skipped, not guessed, and
counted. Look-alikes are rejected: Express's settings getter `app.get("title")`
(no handler), HTTP clients (`axios.get("/u", cfg)`, `request(app).get("/u")`),
`@mock.patch("os.path.join")`, Spring `@FeignClient` interfaces (outgoing
calls declared with the same annotations), and files that use another
framework (Sanic, Koa, Fastify, ...). Python route paths must start with `/`,
as FastAPI and Flask require.

**Validation on real code.** Run against this project's own FastAPI backend,
detection reproduced FastAPI's runtime route table exactly (10 of 10 routes,
none missed, none invented). Run over about 10,500 installed Python files and
15,000 real JavaScript/TypeScript library files it reported only genuine
routes — the scan is what exposed the `@mock.patch` false positive, now fixed
and covered by a regression test.

**Limitations.** Mount prefixes are resolved only when the router and the
mount are in the same file with a literal prefix (see Phase 8C below); a router
mounted from another file keeps its declared path. Chained
`router.route("/p").get(...)`, mappings inherited from a base class, Spring
WebFlux functional routes, NestJS/Koa/Fastify and dynamically generated routes
are out of scope.

## Frontend ↔ backend API mapping (Phase 8C)

Phase 8C connects the two sides of a full-stack project: the calls the
frontend makes (`analyzer/api_calls.py`) and the endpoints the backend declares
(`analyzer/api_endpoints.py`), joined by `analyzer/api_matching.py`. As in 8A
and 8B it is additive, static-analysis only, and not yet exposed through the
upload endpoint, database, API response or UI. Everything it produces is a
**static-analysis finding, not a runtime fact**: an unmatched call is a
*potential integration issue*, an unmatched endpoint is *potentially unused*
("could not find a matching frontend consumer"), and the report says so.

```python
analysis = analyze_project(root, retain_parsed=True)
report = analyze_api_mapping(analysis.parsed_modules, analyze_architecture(analysis.modules))
report.to_dict()   # total_calls, total_endpoints, matched, unmatched_frontend_calls, ...
```

**Mount-prefix resolution (same file).** The 8B detector now resolves a
router's prefix when the mount is a literal in the same file as the router:
Express `app.use("/api", router)` (also arrays of literals and chains such as
`v1.use("/users", users)`), FastAPI `app.include_router(r, prefix="/api")`
(joined before the router's own prefix) and Flask
`app.register_blueprint(bp, url_prefix="/api")` (replaces the blueprint's own
prefix, as Flask does). A router mounted under several prefixes yields one
endpoint per prefix. A prefix that is not a literal is **not guessed**: the
endpoint keeps its path without it and drops to MEDIUM confidence. Routers
mounted from another file are not followed.

**Call detection.** From the JS/TS token stream of the pipeline's
`ParsedModule` (comments and strings are already separated from code):
`fetch(url[, { method }])` (also `window.fetch`), `axios.get/post/put/patch/
delete/head/options(url)`, `axios({ url, method })` / `axios.request(...)`,
axios instances (`axios.create({ baseURL })`), `xhr.open("GET", url)`, and
conventionally named client wrappers (`api.get("/x")`, library `unknown`,
always LOW). An `ApiCall` records `method`, `path`, `source_file`,
`line_number`, `language`, `library`, `confidence`, plus the file's 8A `layer`
and an `external` flag; `ApiCallSummary` reports `total_calls`,
`calls_by_method`, `calls_by_library`, `calls_by_language`,
`calls_by_confidence` and what was skipped.

**URLs and normalization.** A URL is used only if it can be rebuilt statically
from string literals, template literals (each `${expr}` becomes the parameter
`{}`, so `/users/${id}` is `/users/{}`), `+` concatenation, same-file `const`
strings (`const API = "/api"`) and an environment override with a literal
fallback (`import.meta.env.X || "http://localhost:8000"`, MEDIUM). Query string,
fragment and host are dropped (no host is stored); a non-local host marks the
call `external` (LOW). Anything else — `"/users/" + id`, a variable, a call — is
skipped and counted, never guessed. Static segments stay static
(`/users/123` is not turned into `/users/{}`); only explicit parameter syntax
is normalized when matching: `:id`, `{id}`, `{id:int}`, `<int:id>`, `${id}`.

**Layers.** With 8A architecture information, call detection searches
frontend, shared and unknown files only; backend, api, database, tests and
configuration files are not searched, and outside a clearly frontend file a
file importing a server framework or Node-only HTTP client (express, koa,
node-fetch, fs, ...) is skipped as server-side. `axios.get` in backend code is
not a frontend call.

**Matching.** Method equal (an `ANY` endpoint matches all) and path *shape*
equal. Results fall into four categories: `MATCHED`, `UNMATCHED_FRONTEND_CALL`,
`UNUSED_BACKEND_ENDPOINT` and `UNCERTAIN` (an external-host URL, an
unidentified client wrapper, a project with no detected endpoints, or no
detected calls). Each result carries evidence — frontend file, line and call;
backend file, line and endpoint — and a reason. `/api/users` does **not**
match `/users` unless mount analysis proved the prefix; the unmatched finding
then says the backend declares the path "without the leading /api" and that a
mount may be unresolved. A literal segment may satisfy a parameter segment
(`/users/123` ↔ `/users/{id}`, LOW); the reverse does not match, and wildcard
endpoints do not swallow every call.

**Match confidence.** The match quality gives the start — exact path text HIGH,
normalization required MEDIUM, literal-for-parameter LOW — capped at MEDIUM
unless the layers are clear (call in the frontend layer, endpoint in the
backend/api layer), at MEDIUM for `ANY`, at LOW for an external host, and never
above the confidence of the call or the endpoint themselves. Findings about the
*absence* of a match are never above MEDIUM, and LOW when a prefix hint
suggests an unresolved mount.

**Validation on real code.** On this project's own full stack (React frontend,
FastAPI backend) all 9 frontend calls matched the right backend routes, with
file and line on both sides, and the single unused endpoint is the backend's
root banner route `GET /`; every match is MEDIUM because the frontend base URL
is an environment override. Over about 15,000 real library files the call
detector reported 2 calls, both genuine requests to external hosts (flagged
`external`, LOW), and skipped 83 non-static expressions.

**Limitations.** Static analysis cannot see consumers outside the analyzed
code (mobile apps, other services, scripts) or URLs built at runtime, so
"potentially unused" and "potential integration issue" are hints, not
verdicts. Not resolved: mount prefixes defined in another file, interceptors
and proxies, GraphQL and websockets, jQuery/`ky`/`got`/`superagent`,
`fetch(new Request(...))`, a non-literal `method`, `"/users/" + id`-style
concatenation, and Express wildcard routes. Path comparison is exact-case.
Architecture health scoring, the dashboard and AI recommendations are later
phases (8D and beyond).

## Architecture Intelligence integration (Phase 8D)

Phase 8D wires the 8A–8C analyzers into the upload pipeline, the database, the
API and the dashboard. No analyzer was rebuilt; `analyzer/architecture_report.py`
only *assembles* their output.

**Upload.** `analyze_project(root, retain_parsed=True)` keeps the single parse
the metric pipeline already did. `build_architecture_report(modules,
parsed_modules)` runs `analyze_architecture` (8A) and `analyze_api_mapping`
(8B endpoints + 8C calls + matching) over it and returns one JSON report. The
parse trees are released right after. If that step raises, the failure is
logged, the metric scan still succeeds, and the scan records
`status="failed"` — an empty "successful" result is never stored.

**Persistence.** Table `architecture_scans` (model `ArchitectureScan`), one row
per scan, `project_id` unique. Every upload is its own `projects` row, so the
result is tied to exactly the scan that produced it; re-uploading the same
name never touches an older scan's result. The full report is in `report_json`;
headline counts are also stored as columns. The table is created by
`create_all`, so existing databases gain it on the next start; older scans simply
have no row.

**API.** `GET /api/projects/{project_id}/architecture` returns
`{project_id, scan_id, project_name, available, scanned_at, status, message,
architecture}`. 404 for an unknown project. `available: false` (HTTP 200, with
a message) for scans made before 8D or a failed architecture step. It is a pure
read; nothing is recomputed because the uploaded source is deleted after the
scan. The upload response also carries a compact `architecture` headline.

**Report sections.** `project_type` (full-stack / frontend-only / backend-only /
undetermined, from layer and API evidence only, with `project_type_basis`),
`confidence`, `layers` (files, LOC, languages, confidence), `files` (per-file
layer + evidence), `endpoints`, `calls`, `api_mapping` (matched, unmatched
frontend calls = *potential integration issues*, unused endpoints =
*potentially unused*, uncertain; each with `file:line` evidence for both
sides), `uncertainties` (uncertain API findings, low-confidence layers, files
with no layer evidence, dynamic URLs/paths the detectors skipped, parse-error
files), `diagram` and `coverage`.

**Diagram.** The backend returns a `{nodes, edges}` graph projected from the
results above: one node per layer that received files, one edge per
(call layer -> endpoint layer) with matched calls. The frontend
(`utils/architectureDiagram.js`) turns that graph into an SVG string; the same
string is shown in the dashboard and downloaded by "Export diagram SVG", so
the export is exactly the rendered diagram. All labels are XML-escaped. No arrow
is drawn for relationships the analyzers do not examine (for example database
access).

**Limits.** Findings are static-analysis observations, not runtime facts; the
8A–8C limitations above apply unchanged. Layer detection is conventional
(directory/file names), so projects without conventional names show their files
as `unknown` and may be `undetermined`. Architecture results are not part of
the PDF report or the AI Advisor context yet.

## Project identity and version history (Phase 9A)

**Problem.** Until Phase 8D every upload was a separate `projects` row and
"history" was "rows with the same exact ZIP name". That silently merged
unrelated projects that shared a file name and lost the history when a ZIP was
renamed (`app-v2.zip`, a renamed root folder).

**Model.** `projects` rows are unchanged and remain the per-upload scan
snapshots (every existing endpoint is still keyed by that scan id, called
`project_id`). Two tables were added (created automatically by `create_all`, no
existing table was altered):

* `project_identities` - the stable project: display name, normalized
  `name_keys`, created/updated timestamps.
* `project_versions` - one immutable row per successful upload: `identity_id`,
  `scan_id` (unique), `version_number` (unique per identity), upload time, health
  score/status/category scores, average complexity, average maintainability,
  duplication %, average/max risk + module-risk counts, LOC, file/class/function
  counts, per-language `{files, loc}` breakdown, architecture project type, the
  structure fingerprint used for matching and how the version was placed
  (`explicit` / `auto-matched` / `new` / `backfilled`). Detailed per-module metrics
  stay in `modules` and the architecture result in `architecture_scans`, both keyed
  by the version's `scan_id`, so older versions keep their full results.

**Immutability and atomicity.** A version is written in the same transaction as
its scan, modules and architecture result (`_persist_analysis`), so a failed or
rejected upload leaves nothing behind. An ORM `before_update` guard rejects any
UPDATE of a version row. Version numbers are `max + 1` inside the transaction;
the unique constraint makes a concurrent upload lose cleanly and retry.

**Choosing the project (`POST /api/projects/upload` form fields).**
`identity_id` adds a version to that existing project (honored even when the
structure differs, with a warning); `create_new=true` (optionally `project_name`)
forces a new project; with neither, the server decides:

1. Names are normalized (lower-case, trailing version/copy markers such as
   `-v2`, `_final`, `(1)`, dates, `-main` removed). Generic names (`download`,
   `project`, `src`, `backend`, ...) never identify a project.
2. Name keys come from the ZIP file name **and** the ZIP's root folder.
3. Structure = the set of source-file paths. One shared top-level folder is
   ignored (both the stripped and as-uploaded forms are compared and the best
   counts), so renaming the root folder changes nothing. Similarity is the path
   Jaccard index.
4. A project is attached **automatically only if** a non-generic name key matches
   **and** structure similarity >= 0.4, and exactly one project qualifies. A
   matching name alone, or a similar structure alone, only produces a
   *suggestion*; several equal matches are treated as ambiguous and create a new
   project. The response (`version.suggestions`) lists the candidates with
   reasons.

`POST /api/projects/suggest-match` runs only the scan/structure step (nothing is
saved) so the UI can show the same suggestions before uploading.

**APIs.** `GET /api/identities` (projects with latest version),
`GET /api/identities/{id}` (all versions), `GET /api/projects/{scan_id}/history`
(now identity-based; keeps `scan_id, scanned_at, health_score, health_status,
category_scores` and adds `version_number`, `metrics`, `languages`,
`architecture_project_type`; timestamps are UTC with `Z`), and `GET
/api/projects` gains `identity_id` and `version_number`.

**Migration.** On startup `init_db()` runs an idempotent backfill: each earlier
scan without a version is grouped by *exact name* (the old definition of "same
project", so existing history is preserved), numbered by upload date and given a
version rebuilt from its stored module rows with the same definitions the engine
uses. Project-level duplication percentage was never stored per scan, so it is
NULL (shown as N/A), not approximated.

**Frontend.** The project picker lists projects (latest version) and a version
selector opens older versions. `HistoryTrends` draws V1, V2, V3... from the
history response with a selectable metric (health, complexity, maintainability,
duplication, risk) plus the category trend, a value/change table, a first-version
message for single-version projects and gaps (never zeros) for unrecorded values.
`utils/trendData.js` holds the pure data preparation. The upload panel offers
auto / new project / new version of an existing project and shows suggestions.

**Limits.** There is no endpoint to delete, rename or move a version, so a wrong
explicit or automatic attachment cannot be undone from the UI. Structure
matching uses source-file paths only (no content similarity). Versions of
legacy scans may lack some numbers (see Migration). Trends compare uploads, not
git history.

## Version comparison and file-level regression intelligence (Phase 9B)

**What it does.** Compares any two saved versions of the *same* project
(`GET /api/identities/{identity_id}/compare?from=<n>&to=<m>`; without parameters
the latest version against its immediate predecessor; reversed arguments are
reordered oldest -> newest and flagged `swapped`). A project with one version
returns 200 with `available: false` and an explanation (the dashboard's
first-version empty state). 404: unknown project/version; 400: the same
version twice, or no earlier version.

**Design.** `analyzer/comparison.py` is a pure module (no database, no I/O). The
endpoint in `api/identities.py` loads what each upload stored - the version
snapshot, its `modules` rows, its `file_fingerprints` and its saved
architecture report - and hands it to `compare_versions`. Nothing is
re-analyzed and no metric is recomputed with a second formula; the existing
metric and scoring engines are untouched.

**Project-level cards.** For health score, average complexity, average
maintainability, duplication % (and duplicated lines), LOC, files, classes,
functions, documentation (the stored documentation category score plus the
comment-to-code ratio over the stored module rows), average/max risk, high+critical
risk modules, the risk distribution per level, every health category and the
language breakdown: previous, current, absolute change, percent change and a
direction (`improved` / `regressed` / `unchanged` / `changed` for LOC-like
metrics / `unavailable`). A missing value is `unavailable`, never 0; the percentage
is `null` with a reason when the previous value is missing or 0.

**Files.** Paths are normalized (forward slashes, no `./`), and a single shared
top-level ZIP root folder is ignored on whichever side has one when - and only
when - that gives the best path overlap, so `proj-v1/...` -> `proj-v2/...` does not
turn every file into "added" + "removed" (reported in `path_alignment`). Statuses:

* `added`, `removed`;
* `changed` - content hash differs, or (no hash) a tracked metric differs;
* `unchanged` - the stored SHA-256 is identical (verified);
* `unverified` - no tracked metric changed but no hash proves it (older scans).

New table `file_fingerprints` (scan_id, file_path, sha256, size_bytes) is filled
during upload in the scan's transaction because the source ZIP is deleted afterwards.

**Rules (transparent, deterministic).** Published in every response under `rules`
and shown in the UI. A file rule fires when the metric exists in both versions
and the change meets its minimum: total complexity +/-5 and 20%, most complex
function +/-5, maintainability +/-5, coupling +/-3, cohesion (LCOM1, lower is better)
+/-3, duplication +/-10 points, risk score +/-10, plus any move between
LOW/MEDIUM/HIGH/CRITICAL. Verdict: `regressed`, `improved`, `mixed` (both) or
`neutral`. A file with a parse error in either version, or a missing metric, is not
judged on what is missing (and says so). New HIGH/CRITICAL files are regressions.
Project rules use minimum changes of 5 (health, maintainability, documentation,
average risk), 0.5 and 10% (complexity), 2 points (duplication) and 1 module
(high-risk count); severity is `high` at twice the minimum.

**Renames - disclosed, never assumed.** Removed + added files are paired
one-to-one and reported as *possible renames*: `high` = identical content hash,
`medium` = same name, language and identical size/structure metrics, `low` = same
name and language, similar size. They stay counted as added and removed files,
carry a note on each row, and appear in `uncertainty`.

**Architecture/API.** When both versions saved an architecture analysis, layers,
endpoints, calls, matched calls, unmatched calls and potentially unused endpoints
are diffed by their `METHOD /path` identity with `file:line` evidence; new
unmatched calls are regressions, resolved ones improvements. Otherwise the
response says which version lacks it.

**Frontend.** `VersionComparison.jsx` (+ `utils/comparisonView.js`): version
selectors, before/after metric cards, regression alerts, improvement highlights,
uncertainty list, a file table with language / risk / status / verdict / path
filters, the architecture diff and the rules.

**Limits.** "Unchanged" cannot be proven for scans stored before Phase 9B.
Renames are only ever suggested. Regression thresholds are fixed project-defined
values, not statistically derived. Comparison compares uploads, not git history.
Comparison results are not part of the PDF report or the AI Advisor context.

## Implementation status

- **Phase 1 (done):** app boots, database connects, frontend reaches backend.
- **Phase 2 (done):** Project Upload -> Project Scanner -> Source Code
  Parser -> Metric Engine are fully implemented and produce structured,
  persisted results (`POST /api/projects/upload`). See `docs/metrics.md`.
- **Phase 3 (done):** Quality Analysis -> Health Score -> Risk Detection
  are fully implemented, rule-based (no AI/ML), and layered on top of the
  Phase 2 metrics in the same upload response. See `docs/health-score.md`.
- **Phase 4 (done):** the project/metrics/risks read APIs above, a global
  error handler, and database-quality verification (FK relationship,
  cascade delete, JSON round-tripping, null-metric persistence).
- **Phase 5 (done):** the React dashboard — project list/switching,
  health gauge, category-score chart, risk distribution, risky-module
  table, module detail. Hand-rolled SVG, no charting library, no new
  dependencies. See the frontend `README.md`.
- **Phase 6A (done):** the AI Advisor backend foundation — see
  `docs/ai-advisor.md`. Explains persisted metrics/health/risk in plain
  language; never computes them. (Phase 6B added the Ollama provider,
  6C the frontend recommendation UI, 6D a final acceptance pass — all
  documented in `docs/ai-advisor.md`.)
- **Phase 7A (done):** scan history — `GET /api/projects/{id}/history`
  plus `frontend/src/components/HistoryTrends.jsx`, both described above.
- **Phase 7B+:** PDF reports, the Project Health Certificate.
- **Phase 8A (done):** Project Architecture Intelligence foundation — layer
  classification of analyzed files (`analyzer/architecture.py`), described
  above. Additive and not yet exposed through the API or UI.
- **Phase 8B (done):** API endpoint detection for FastAPI, Flask, Spring and
  Express (`analyzer/api_endpoints.py`), described above, plus the opt-in
  `analyze_project(retain_parsed=True)` hook.
- **Phase 8C (done):** same-file mount-prefix resolution, frontend API-call
  detection (`analyzer/api_calls.py`) and frontend/backend matching
  (`analyzer/api_matching.py`), described above. The UI (8D) follows.
- **Phase 8D (done):** Architecture Intelligence is integrated end to end —
  analysis during upload, SQLite persistence (`architecture_scans`),
  `GET /api/projects/{id}/architecture`, the dashboard section
  (`frontend/src/components/ArchitectureIntelligence.jsx`) and SVG diagram
  export. See "Architecture Intelligence integration (Phase 8D)" above.
- **Phase 9A (done):** stable project identity, immutable version history,
  repeated-upload tracking and working version trends. See "Project identity and
  version history (Phase 9A)" above.
- **Phase 9B (done):** version-to-version comparison, file-level regression
  intelligence, content fingerprints and the Version Comparison dashboard. See
  "Version comparison and file-level regression intelligence (Phase 9B)" above.
