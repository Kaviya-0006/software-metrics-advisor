# software-metrics-advisor: TypeScript backend

Strict TypeScript (ES modules, Node >= 20.11), Fastify 5, Prisma + SQLite, tree-sitter, Ollama.

## Run

```bash
npm install                    # also runs `prisma generate`
cp .env.example .env
npx prisma db push             # creates prisma/software_metrics.db
npm run db:seed                # optional demo rows
npm run dev                    # http://localhost:8000/api/projects

# no database yet? serve built-in sample data:
USE_MOCK_DB=true npm run dev

npm run verify                 # typecheck + tests + build
```

The React frontend needs no changes: port 8000, same snake_case JSON, errors as `{ "detail": "..." }`.

## Layout

```
prisma/schema.prisma        Project, Module, MetricSnapshot (same table/column names as the Python app)
prisma/seed.ts
src/
  server.ts                 entry: open DB, listen, graceful shutdown
  app.ts                    composition root: CORS, hooks, global error handler, wiring
  config/env.ts             typed settings                       (was app/config.py)
  types/metrics.ts          domain interfaces: LOC, complexity, MI, risk, reports
  types/api.ts              wire (snake_case) DTOs the React app consumes
  domain/bands.ts           score -> risk level / health status
  validation/guards.ts      runtime type guards (no `as` casts)
  errors/AppError.ts        client-safe error + contract-violation error
  database/
    ProjectDb.ts            the slice of PrismaClient we depend on
    ProjectRepository.ts    all queries
    MockPrismaClient.ts     in-memory stand-in (tests, USE_MOCK_DB)
    connection.ts           picks Prisma or mock
  services/ProjectService.ts
  routes/projects.ts        GET /api/projects                    (was api/projects.py)
  analyzer/Engine.ts        LOC streaming, tree-sitter complexity, maintainability index
  ai/OllamaAdvisor.ts       Ollama client, never throws
tests/                      vitest: engine, route (app.inject), CORS, error paths
```

Dependencies point downward only: routes -> services -> repositories -> ProjectDb (Prisma | mock).

## Design decisions

- **Repositories depend on `ProjectDb`, not on `PrismaClient`.** The generated client satisfies it
  structurally (checked at the one assignment in `connection.ts`), and so does the mock. Tests run
  without a database, and `@prisma/client` is imported dynamically so `USE_MOCK_DB=true` works
  before `prisma generate`.
- **Runtime validation instead of casts.** SQLite is dynamically typed, so a FLOAT column can
  contain a string. The route validates the payload against the TypeScript interface with a type
  guard before sending; a violation is logged and the client gets a generic 500.
- **Two layers of error handling.** Services convert infrastructure failures to `AppError`
  (safe message, original kept as `cause`); `setErrorHandler` in `app.ts` maps everything to
  `{ detail }` and never leaks internals for unexpected errors.
- **tree-sitter replaces radon and javalang.** One query per language captures functions and
  decision points in a single pass. Decisions are attributed to the nearest enclosing function,
  so nested functions can score slightly differently from radon.
- **Halstead volume comes from tree-sitter tokens**, so the maintainability index is close to,
  but not identical with, the Python backend's values.
- **`parse()` is synchronous CPU work.** For big uploads run `Engine` in `worker_threads`.
- **Package name:** there is no `@ollama/sdk`; the official package is `ollama`.

## Reusing the Python database

Table and column names match, but Prisma parses `DateTime` columns itself. Convert the old
timestamps first, on a copy of the file:

```sql
UPDATE projects SET upload_date = replace(upload_date, ' ', 'T') || 'Z';
```

## Not ported yet

Upload and scan pipeline, scoring, risk detection, coupling/cohesion/duplication, the PDF report and
the remaining `/api/projects/:id/*` endpoints. Each follows the same route -> service -> repository shape.
