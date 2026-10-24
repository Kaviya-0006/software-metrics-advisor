/**
 * Data-access layer: the ONLY place that contains SQL for projects.
 * Services never see statements or better-sqlite3 types, so the storage
 * engine can change without touching business logic or routes.
 */
export class ProjectRepository {
  #listAll;
  #findById;

  /** @param {import('better-sqlite3').Database} db */
  constructor(db) {
    // Prepared once, reused for every request.
    this.#listAll = db.prepare(
      `SELECT id, name, upload_date, health_score, health_status, languages, files_analyzed
         FROM projects
        ORDER BY upload_date DESC, id DESC`,
    );
    this.#findById = db.prepare(
      `SELECT id, name, upload_date, health_score, health_status, languages, files_analyzed
         FROM projects WHERE id = ?`,
    );
  }

  /** All projects, most recently uploaded first (async-shaped on purpose). */
  async findAll() {
    return this.#listAll.all();
  }

  async findById(id) {
    return this.#findById.get(id) ?? null;
  }
}
