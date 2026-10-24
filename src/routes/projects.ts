import type { FastifyPluginAsync } from 'fastify';
import type { ProjectService } from '../services/ProjectService.js';
import type { ProjectListResponse } from '../types/api.js';
import { assertProjectListResponse } from '../validation/guards.js';

/**
 * Project routes (replaces app/api/projects.py).
 *
 * A Fastify plugin mounted with `{ prefix: '/api/projects' }` in app.ts, so
 * the path here is '/', i.e. GET /api/projects (FastAPI's APIRouter(prefix=...)).
 *
 * CORS is configured once, globally, in app.ts, so every route gets the same
 * policy including pre-flight OPTIONS handling.
 *
 * No try/catch in handlers: Fastify awaits async handlers and routes any
 * rejection to the global error handler in app.ts.
 */

const projectSummarySchema = {
  type: 'object',
  required: ['id', 'name', 'upload_date', 'health_score', 'health_status', 'files', 'languages'],
  properties: {
    id: { type: 'integer' },
    name: { type: 'string' },
    upload_date: { type: ['string', 'null'] },
    health_score: { type: ['number', 'null'] },
    health_status: { type: ['string', 'null'] },
    files: { type: ['integer', 'null'] },
    languages: { type: 'array', items: { type: 'string' } },
  },
} as const;

const projectListResponseSchema = {
  type: 'object',
  required: ['projects'],
  properties: { projects: { type: 'array', items: projectSummarySchema } },
} as const;

export interface ProjectRoutesOptions {
  projectService: ProjectService;
}

export const projectRoutes: FastifyPluginAsync<ProjectRoutesOptions> = async (app, { projectService }) => {
  // The Reply generic makes the handler's return type checked at compile time.
  app.get<{ Reply: ProjectListResponse }>(
    '/',
    {
      schema: {
        summary: 'List analyzed projects, most recently uploaded first',
        // Fastify serialises with fast-json-stringify against this schema
        // (faster than JSON.stringify) and drops undeclared fields.
        response: { 200: projectListResponseSchema },
      },
    },
    async () => {
      // Deliberately typed `unknown`: the data came from SQLite, so we prove
      // its shape at runtime against the TypeScript interface before sending.
      // A mismatch throws ResponseContractError -> logged, generic 500.
      const payload: unknown = { projects: await projectService.listProjects() };
      assertProjectListResponse(payload);
      return payload;
    },
  );

  // Next endpoints to port (same route -> service -> repository shape):
  //   GET /:id  /:id/metrics  /:id/risks  /:id/recommendations  /:id/history  /:id/report
  //   POST /upload (use @fastify/multipart instead of python-multipart)
};
