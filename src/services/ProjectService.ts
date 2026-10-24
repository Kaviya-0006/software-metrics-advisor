import type { ProjectRecord } from '../database/ProjectDb.js';
import type { ProjectRepository } from '../database/ProjectRepository.js';
import { AppError } from '../errors/AppError.js';
import type { ProjectSummaryDto } from '../types/api.js';

/** Minimal logger contract; Fastify's pino logger satisfies it. */
export interface Logger {
  error(obj: object, msg?: string): void;
}

const toDto = (row: ProjectRecord): ProjectSummaryDto => ({
  id: row.id,
  name: row.name,
  upload_date: row.uploadDate?.toISOString() ?? null,
  health_score: row.healthScore,
  health_status: row.healthStatus,
  files: row.filesAnalyzed,
  languages: row.languages ? row.languages.split(',').filter(Boolean) : [],
});

/**
 * Business-logic layer. It owns error translation: infrastructure failures
 * are logged with full detail and re-thrown as a client-safe AppError, so
 * route handlers stay free of try/catch noise while errors are still handled
 * in exactly one place per layer.
 */
export class ProjectService {
  private readonly repository: ProjectRepository;
  private readonly log: Logger;

  constructor(repository: ProjectRepository, log: Logger) {
    this.repository = repository;
    this.log = log;
  }

  async listProjects(): Promise<ProjectSummaryDto[]> {
    try {
      const rows = await this.repository.findAll();
      return rows.map(toDto);
    } catch (err) {
      this.log.error({ err }, 'Failed to load projects from the database');
      throw new AppError(500, 'Could not load projects.', { cause: err });
    }
  }
}
