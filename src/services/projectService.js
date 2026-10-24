import { AppError } from '../errors/AppError.js';
import { projectFromRow, projectToDto } from '../database/schema.js';

/**
 * Business-logic layer (the body of the old FastAPI path functions, minus
 * HTTP). It owns error translation: database failures are logged with full
 * detail and re-thrown as a safe AppError, so route handlers stay free of
 * try/catch noise while errors are still handled in exactly one place.
 */
export class ProjectService {
  #repository;
  #log;

  /**
   * @param {import('../database/repositories/projectRepository.js').ProjectRepository} repository
   * @param {{ error: Function }} log  a pino-compatible logger (Fastify's app.log)
   */
  constructor(repository, log) {
    this.#repository = repository;
    this.#log = log;
  }

  /** @returns {Promise<object[]>} project summaries in the wire (snake_case) format */
  async listProjects() {
    try {
      const rows = await this.#repository.findAll();
      return rows.map((row) => projectToDto(projectFromRow(row)));
    } catch (err) {
      this.#log.error({ err }, 'Failed to load projects from the database');
      throw new AppError(500, 'Could not load projects.', { cause: err });
    }
  }
}
