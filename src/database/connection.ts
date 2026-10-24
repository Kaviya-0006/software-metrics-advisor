import type { AppConfig } from '../config/env.js';
import { MockPrismaClient } from './MockPrismaClient.js';
import type { DatabaseHandle } from './ProjectDb.js';

/**
 * Chooses the database implementation.
 *
 * `@prisma/client` is imported dynamically so that USE_MOCK_DB=true works on a
 * fresh checkout, before `prisma generate` has produced the client. The
 * assignment `client: prisma` is also where TypeScript verifies that the
 * real, generated PrismaClient still satisfies the ProjectDb contract.
 */
export async function openDatabase(config: AppConfig): Promise<DatabaseHandle> {
  if (config.useMockDb) return new MockPrismaClient().asHandle();

  const { PrismaClient } = await import('@prisma/client');
  const prisma = new PrismaClient();
  try {
    await prisma.$connect(); // fail fast at startup, not on the first request
  } catch (err) {
    await prisma.$disconnect();
    throw err;
  }
  return { client: prisma, close: () => prisma.$disconnect() };
}
