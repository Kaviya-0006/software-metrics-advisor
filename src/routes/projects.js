/**
 * Project routes  (replaces app/api/projects.py)
 *
 * A Fastify *plugin*: an async function that receives the encapsulated
 * instance plus options. It is mounted in app.js with
 * `{ prefix: '/api/projects' }`, which is why the path below is just '/'
 * (-> GET /api/projects), mirroring FastAPI's APIRouter(prefix=...).
 *
 * CORS is deliberately NOT configured here. It is registered once, globally,
 * in app.js (@fastify/cors), so every route - present and future - gets the
 * same policy, including pre-flight OPTIONS handling.
 *
 * No try/catch is needed in handlers: Fastify awaits async handlers and
 * routes any thrown/rejected error to the global handler in app.js
 * (the equivalent of FastAPI's @app.exception_handler).
 */

const projectSummary = {
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
};

/**
 * @param {import('fastify').FastifyInstance} app
 * @param {{ projectService: import('../services/projectService.js').ProjectService }} opts
 */
export default async function projectRoutes(app, { projectService }) {
  app.get(
    '/',
    {
      schema: {
        summary: 'List analyzed projects, most recently uploaded first',
        // A response schema makes Fastify serialise with fast-json-stringify
        // (considerably faster than JSON.stringify) and guarantees the
        // payload shape the React client depends on.
        response: {
          200: {
            type: 'object',
            required: ['projects'],
            properties: { projects: { type: 'array', items: projectSummary } },
          },
        },
      },
    },
    async () => ({ projects: await projectService.listProjects() }),
  );

  // Next endpoints to port (same pattern: route -> service -> repository):
  //   GET /:id  /:id/metrics  /:id/risks  /:id/recommendations  /:id/history  /:id/report
  //   POST /upload  (use @fastify/multipart instead of python-multipart)
}
