import type { FastifyInstance } from 'fastify';
import { afterEach, describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config/env.js';
import { MockPrismaClient } from '../src/database/MockPrismaClient.js';
import type { ProjectDb } from '../src/database/ProjectDb.js';
import { buildApp } from '../src/app.js';
import { isProjectListResponse } from '../src/validation/guards.js';

const config = loadConfig({ CORS_ORIGINS: 'http://localhost:5173' });
let app: FastifyInstance | undefined;

async function start(db: ProjectDb): Promise<FastifyInstance> {
  app = await buildApp({ config, db, logger: false });
  return app;
}

afterEach(async () => {
  await app?.close();
  app = undefined;
});

describe('GET /api/projects', () => {
  it('returns the contract the React client expects, newest first', async () => {
    const res = await (await start(new MockPrismaClient())).inject({ method: 'GET', url: '/api/projects' });
    expect(res.statusCode).toBe(200);

    const body: unknown = res.json();
    expect(isProjectListResponse(body)).toBe(true);
    if (!isProjectListResponse(body)) return;
    expect(body.projects.map((p) => p.name)).toEqual(['demo_java', 'demo_python', 'legacy_mixed']);
    expect(body.projects[0]?.languages).toEqual(['java']);
    expect(body.projects[2]?.upload_date).toBeNull();
  });

  it('sends CORS headers for an allowed origin, not for others', async () => {
    const server = await start(new MockPrismaClient());
    const ok = await server.inject({ method: 'GET', url: '/api/projects', headers: { origin: 'http://localhost:5173' } });
    expect(ok.headers['access-control-allow-origin']).toBe('http://localhost:5173');
    const bad = await server.inject({ method: 'GET', url: '/api/projects', headers: { origin: 'http://evil.example' } });
    expect(bad.headers['access-control-allow-origin']).toBeUndefined();
  });

  it('a database failure becomes a safe {detail} 500 that leaks no internals', async () => {
    const db = new MockPrismaClient({ failWith: new Error('SQLITE_CORRUPT: secret path /var/db') });
    const res = await (await start(db)).inject({ method: 'GET', url: '/api/projects' });
    expect(res.statusCode).toBe(500);
    expect(res.json()).toEqual({ detail: 'Could not load projects.' });
  });

  it('data that violates the contract is caught before it reaches the client', async () => {
    const bad = new MockPrismaClient({
      rows: [{ id: 1, name: 'x', uploadDate: null, healthScore: Number.NaN, healthStatus: null, languages: null, filesAnalyzed: null }],
    });
    const res = await (await start(bad)).inject({ method: 'GET', url: '/api/projects' });
    expect(res.statusCode).toBe(500);
    expect(res.json()).toEqual({ detail: 'An unexpected error occurred while processing the request.' });
  });

  it('unknown routes return {detail: "Not Found"}', async () => {
    const res = await (await start(new MockPrismaClient())).inject({ method: 'GET', url: '/nope' });
    expect(res.statusCode).toBe(404);
    expect(res.json()).toEqual({ detail: 'Not Found' });
  });
});
