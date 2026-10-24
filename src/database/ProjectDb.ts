/**
 * The narrow slice of the Prisma client this application depends on.
 *
 * Repositories are written against THIS interface, not against PrismaClient.
 * The generated PrismaClient satisfies it structurally, and so does
 * MockPrismaClient, which gives us:
 *   - tests and local dev with no database and no `prisma generate`
 *   - a compile-time check that real and mock stay interchangeable
 *   - one place to widen when more queries are ported
 */

export type SortOrder = 'asc' | 'desc';

/** A `projects` row as selected by the repository (matches schema.prisma). */
export interface ProjectRecord {
  id: number;
  name: string;
  uploadDate: Date | null;
  healthScore: number | null;
  healthStatus: string | null;
  languages: string | null;
  filesAnalyzed: number | null;
}

export interface ProjectSelect {
  id: true;
  name: true;
  uploadDate: true;
  healthScore: true;
  healthStatus: true;
  languages: true;
  filesAnalyzed: true;
}

export interface ProjectOrderBy {
  uploadDate?: SortOrder;
  id?: SortOrder;
}

export interface ProjectFindManyArgs {
  select: ProjectSelect;
  orderBy: ProjectOrderBy[];
}

export interface ProjectDb {
  project: {
    findMany(args: ProjectFindManyArgs): Promise<ProjectRecord[]>;
  };
}

/** What `connection.ts` hands to the composition root. */
export interface DatabaseHandle {
  client: ProjectDb;
  close(): Promise<void>;
}
