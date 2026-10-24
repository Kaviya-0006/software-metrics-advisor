import type { ProjectDb, ProjectRecord } from './ProjectDb.js';

/**
 * Data-access layer: the only place that knows how projects are queried.
 * Services depend on this class, never on Prisma.
 */
export class ProjectRepository {
  private readonly db: ProjectDb;

  constructor(db: ProjectDb) {
    this.db = db;
  }

  /** All projects, most recently uploaded first (id breaks ties). */
  async findAll(): Promise<ProjectRecord[]> {
    return this.db.project.findMany({
      select: {
        id: true,
        name: true,
        uploadDate: true,
        healthScore: true,
        healthStatus: true,
        languages: true,
        filesAnalyzed: true,
      },
      orderBy: [{ uploadDate: 'desc' }, { id: 'desc' }],
    });
  }
}
