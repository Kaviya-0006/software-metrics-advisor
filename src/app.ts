import cors from '@fastify/cors';
import Fastify, { type FastifyInstance } from 'fastify';
import type { AppConfig } from './config/env.js';
import type { ProjectDb } from './database/ProjectDb.js';
import { ProjectRepository } from './database/ProjectRepository.js';
import { AppError } from './errors/AppError.js';
import { projectRoutes } from './routes/projects.js';
import { ProjectService } from './services/ProjectService.js';
import type { ErrorResponse } from './types/api.js';

export interface AppDeps {
  config: AppConfig;
  db: ProjectDb;
  /** Tests pass false to silence logs. */
  logger?: boolean;
}

/**
 * Composition root (replaces app/main.py).
 *
 * Dependencies are injected, never imported as singletons, so tests build the
 * app with MockPrismaClient and call `app.inject()` without opening a port.
 *
 * Layering (dependencies point downward only):
 *     routes -> services -> repositories -> ProjectDb (Prisma | mock)
 */
export async function buildApp({ config, db, logger }: AppDeps): Promise<FastifyInstance> {
  const app = Fastify({
    logger: logger === false ? false : { level: config.env === 'production' ? 'info' : 'debug' },
  });

  // ---- CORS (replaces CORSMiddleware) --------------------------------------
  await app.register(cors, {
    origin: [...config.corsOrigins],
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    // The frontend reads the PDF filename from this header; browsers hide it
    // from cross-origin scripts unless explicitly exposed.
    exposedHeaders: ['Content-Disposition'],
  });

  // ---- Global middleware ------------------------------------------------------
  app.addHook('onSend', async (request, reply, payload) => {
    if (request.url.startsWith('/api/')) void reply.header('Cache-Control', 'no-store');
    return payload;
  });

  // ---- Global error handling ----------------------------------------------------
  // Preserves FastAPI's {"detail": "..."} body: api.js reads `body.detail`.
  app.setErrorHandler((error, request, reply) => {
    if (error instanceof AppError) {
      const body: ErrorResponse = { detail: error.message };
      return reply.status(error.statusCode).send(body);
    }
    // Fastify's own 4xx errors (invalid JSON, payload too large...) are client-safe.
    const status = error.statusCode;
    if (status !== undefined && status >= 400 && status < 500) {
      const body: ErrorResponse = { detail: error.message };
      return reply.status(status).send(body);
    }
    // Anything else is a bug: log everything, tell the client nothing.
    request.log.error({ err: error }, 'Unhandled exception');
    const body: ErrorResponse = { detail: 'An unexpected error occurred while processing the request.' };
    return reply.status(500).send(body);
  });

  app.setNotFoundHandler((_request, reply) => {
    const body: ErrorResponse = { detail: 'Not Found' };
    return reply.status(404).send(body);
  });

  // ---- Wiring -----------------------------------------------------------------------
  const projectService = new ProjectService(new ProjectRepository(db), app.log);

  app.get('/', async () => ({ message: `${config.appName} API`, status: 'running' }));
  app.get('/api/health', async () => ({ status: 'healthy', service: config.serviceId }));
  await app.register(projectRoutes, { prefix: '/api/projects', projectService });

  return app;
}
