import type { DatabaseHandle, ProjectDb, ProjectFindManyArgs, ProjectOrderBy, ProjectRecord } from './ProjectDb.js';

export const SAMPLE_PROJECTS: readonly ProjectRecord[] = [
  {
    id: 1,
    name: 'demo_python',
    uploadDate: new Date('2026-10-20T09:30:00.000Z'),
    healthScore: 78.5,
    healthStatus: 'HEALTHY',
    languages: 'python',
    filesAnalyzed: 3,
  },
  {
    id: 2,
    name: 'demo_java',
    uploadDate: new Date('2026-10-22T14:05:00.000Z'),
    healthScore: 61.2,
    healthStatus: 'NEEDS_ATTENTION',
    languages: 'java',
    filesAnalyzed: 3,
  },
  {
    id: 3,
    name: 'legacy_mixed',
    uploadDate: null, // exercises the nullable paths
    healthScore: null,
    healthStatus: null,
    languages: 'python,java',
    filesAnalyzed: null,
  },
];

export interface MockPrismaOptions {
  rows?: readonly ProjectRecord[];
  /** When set, every query rejects with this error (to test failure handling). */
  failWith?: Error;
}

const timeOf = (row: ProjectRecord): number => row.uploadDate?.getTime() ?? Number.NEGATIVE_INFINITY;

/** Compare by the first ordering key that differs, like SQL ORDER BY a, b. */
function compare(a: ProjectRecord, b: ProjectRecord, orderBy: readonly ProjectOrderBy[]): number {
  for (const order of orderBy) {
    if (order.uploadDate) {
      const diff = timeOf(a) - timeOf(b);
      if (diff !== 0 && !Number.isNaN(diff)) return order.uploadDate === 'asc' ? diff : -diff;
    }
    if (order.id) {
      const diff = a.id - b.id;
      if (diff !== 0) return order.id === 'asc' ? diff : -diff;
    }
  }
  return 0;
}

/**
 * In-memory stand-in for PrismaClient. Implements the same ProjectDb contract,
 * so the repository, service and routes run unchanged against it.
 */
export class MockPrismaClient implements ProjectDb {
  readonly project: ProjectDb['project'];

  constructor(options: MockPrismaOptions = {}) {
    const rows = options.rows ?? SAMPLE_PROJECTS;
    const failWith = options.failWith;
    this.project = {
      findMany: async (args: ProjectFindManyArgs): Promise<ProjectRecord[]> => {
        if (failWith) throw failWith;
        return [...rows].sort((a, b) => compare(a, b, args.orderBy));
      },
    };
  }

  asHandle(): DatabaseHandle {
    return { client: this, close: async () => undefined };
  }
}
