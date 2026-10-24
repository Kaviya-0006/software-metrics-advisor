import cors from '@fastify/cors';
import Fastify from 'fastify';
import { ProjectRepository } from './database/repositories/projectRepository.js';
import { AppError } from './errors/AppError.js';
import projectRoutes from './routes/projects.js';
import { ProjectService } from './services/projectService.js';

/**
 * Composition root (replaces app/main.py).
 *
 * `buildApp` takes its dependencies as arguments instead of importing
 * singletons. Production passes the real config + SQLite connection; tests
 * pass an in-memory database and call `app.inject({ url })`, with no port.
 *
 * Layering (dependencies point downward only):
 *     routes -> services -> repositories -> database
 */
export async function buildApp({ config, db }) {
  const app = Fastify({
    logger: { level: config.env === 'production' ? 'info' : 'debug' },
  });

  // ---- CORS (replaces CORSMiddleware) --------------------------------------
  await app.register(cors, {
    origin: config.corsOrigins,
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    // The frontend reads the PDF filename from this header; browsers hide it
    // from cross-origin scripts unless it is explicitly exposed.
    exposedHeaders: ['Content-Disposition'],
  });

  // ---- Global middleware (hook) --------------------------------------------
  app.addHook('onSend', async (request, reply, payload) => {
    if (request.url.startsWith('/api/')) reply.header('Cache-Control', 'no-store');
    return payload;
  });

  // ---- Global error handling ------------------------------------------------
  // Preserves FastAPI's {"detail": "..."} body: api.js reads `body.detail`.
  app.setErrorHandler((error, request, reply) => {
    if (error instanceof AppError) {
      return reply.status(error.statusCode).send({ detail: error.message });
    }
    // Fastify's own 4xx errors (bad JSON, payload too large, ...) are client-safe.
    if (error.statusCode >= 400 && error.statusCode < 500) {
      return reply.status(error.statusCode).send({ detail: error.message });
    }
    // Anything else is a bug: log everything, tell the client nothing.
    request.log.error({ err: error }, 'Unhandled exception');
    return reply
      .status(500)
      .send({ detail: 'An unexpected error occurred while processing the request.' });
  });

  app.setNotFoundHandler((_request, reply) => reply.status(404).send({ detail: 'Not Found' }));

  // ---- Wiring ----------------------------------------------------------------
  const projectService = new ProjectService(new ProjectRepository(db), app.log);

  app.get('/', async () => ({ message: `${config.appName} API`, status: 'running' }));
  app.get('/api/health', async () => ({ status: 'healthy', service: config.serviceId }));
  await app.register(projectRoutes, { prefix: '/api/projects', projectService });

  return app;
}
