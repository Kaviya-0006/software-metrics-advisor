/**
 * Domain models & schema  (replaces app/database/models.py + schemas.py)
 * =====================================================================
 *
 * The schema is declared ONCE, as plain data. From that single declaration
 * we derive (a) the CREATE TABLE SQL and (b) the row <-> domain mappers, so
 * the two can never drift apart. That is the role SQLAlchemy's declarative
 * models played in the Python backend, without an ORM.
 *
 * MIGRATION NOTES
 * - `projects` and `modules` keep the exact table/column names and types of
 *   the SQLAlchemy models, and every DDL statement is IF NOT EXISTS, so a
 *   `software_metrics.db` produced by the Python backend opens unchanged.
 * - `metric_snapshots` is NEW. The Python app preserved history by
 *   inserting a new `projects` row per upload and grouping by name. A
 *   snapshot table makes "metrics over time" a first-class, queryable
 *   thing (trend charts, regressions) without that name-collision caveat.
 *   It is purely additive: nothing in the old schema is touched.
 * - JSON columns (category_scores, risk_factors, metrics_json) are TEXT in
 *   SQLite, exactly as SQLAlchemy's JSON type stored them.
 * - Timestamps are stored as UTC text. Python wrote "YYYY-MM-DD HH:MM:SS.ffffff"
 *   (naive UTC); we write ISO-8601 with "Z". Both are read by `toIsoUtc`.
 */

// ---------------------------------------------------------------------------
// Declarative table definitions
// ---------------------------------------------------------------------------

/**
 * Column spec: { type, primaryKey?, notNull?, references?, json? }
 *   json: true  -> value is JSON.stringify'd on write and JSON.parse'd on read
 */
export const TABLES = Object.freeze({
  /** One row per uploaded & analysed project scan. */
  projects: {
    columns: {
      id: { type: 'INTEGER', primaryKey: true },
      name: { type: 'VARCHAR', notNull: true },
      upload_date: { type: 'DATETIME' },
      status: { type: 'VARCHAR' }, // workflow: pending | analyzed | failed
      languages: { type: 'VARCHAR' }, // comma-separated, e.g. "python,java"
      files_analyzed: { type: 'INTEGER' },
      total_classes: { type: 'INTEGER' },
      total_functions: { type: 'INTEGER' },
      total_loc: { type: 'INTEGER' },
      health_score: { type: 'FLOAT' },
      health_status: { type: 'VARCHAR' }, // CRITICAL..EXCELLENT band (not workflow status)
      category_scores: { type: 'JSON', json: true },
    },
    indexes: [['name']],
  },

  /** One row per analysed source file within a project scan. */
  modules: {
    columns: {
      id: { type: 'INTEGER', primaryKey: true },
      project_id: { type: 'INTEGER', notNull: true, references: 'projects(id) ON DELETE CASCADE' },
      file_name: { type: 'VARCHAR', notNull: true },
      file_path: { type: 'VARCHAR', notNull: true },
      language: { type: 'VARCHAR', notNull: true },
      loc_total: { type: 'INTEGER' },
      loc_code: { type: 'INTEGER' },
      loc_comments: { type: 'INTEGER' },
      loc_blank: { type: 'INTEGER' },
      classes: { type: 'INTEGER' },
      functions: { type: 'INTEGER' },
      complexity_total: { type: 'INTEGER' },
      complexity_average: { type: 'FLOAT' },
      complexity_maximum: { type: 'INTEGER' },
      maintainability_index: { type: 'FLOAT' },
      coupling: { type: 'INTEGER' },
      cohesion: { type: 'FLOAT' },
      depth_of_inheritance: { type: 'FLOAT' },
      duplication_percentage: { type: 'FLOAT' },
      duplicated_lines: { type: 'INTEGER' },
      parse_error: { type: 'TEXT' },
      risk_score: { type: 'FLOAT' },
      risk_level: { type: 'VARCHAR' }, // LOW | MEDIUM | HIGH | CRITICAL
      risk_factors: { type: 'JSON', json: true }, // string[]
      metrics_json: { type: 'JSON', json: true }, // full nested result, kept for forward-compat
    },
    indexes: [['project_id']],
  },

  /**
   * NEW: point-in-time metrics for one module in one scan. Append-only.
   * `module_id` is nullable so a project-level rollup row can be stored
   * with module_id = NULL.
   */
  metric_snapshots: {
    columns: {
      id: { type: 'INTEGER', primaryKey: true },
      project_id: { type: 'INTEGER', notNull: true, references: 'projects(id) ON DELETE CASCADE' },
      module_id: { type: 'INTEGER', references: 'modules(id) ON DELETE CASCADE' },
      captured_at: { type: 'DATETIME', notNull: true },
      loc_total: { type: 'INTEGER' },
      loc_code: { type: 'INTEGER' },
      complexity_total: { type: 'INTEGER' },
      complexity_maximum: { type: 'INTEGER' },
      maintainability_index: { type: 'FLOAT' },
      risk_score: { type: 'FLOAT' },
      health_score: { type: 'FLOAT' },
      extra: { type: 'JSON', json: true }, // any metric without its own column yet
    },
    indexes: [['project_id', 'captured_at'], ['module_id']],
  },
});

// ---------------------------------------------------------------------------
// DDL generation
// ---------------------------------------------------------------------------

function createTableSql(table, { columns, indexes = [] }) {
  const lines = Object.entries(columns).map(([name, spec]) => {
    const parts = [name, spec.type];
    if (spec.primaryKey) parts.push('PRIMARY KEY AUTOINCREMENT');
    if (spec.notNull) parts.push('NOT NULL');
    if (spec.references) parts.push(`REFERENCES ${spec.references}`);
    return `  ${parts.join(' ')}`;
  });
  const indexSql = indexes.map(
    (cols) => `CREATE INDEX IF NOT EXISTS ix_${table}_${cols.join('_')} ON ${table} (${cols.join(', ')});`,
  );
  return [`CREATE TABLE IF NOT EXISTS ${table} (\n${lines.join(',\n')}\n);`, ...indexSql].join('\n');
}

/** Full DDL, parents first so foreign keys resolve. Idempotent. */
export const SCHEMA_SQL = Object.entries(TABLES)
  .map(([table, def]) => createTableSql(table, def))
  .join('\n\n');

// ---------------------------------------------------------------------------
// Row <-> domain mapping
// ---------------------------------------------------------------------------

/**
 * SQLite/SQLAlchemy wrote naive UTC like "2026-10-23 10:15:00.123456".
 * Return ISO-8601 with an explicit "Z" so browsers don't parse it as local time.
 * @param {string|null|undefined} value
 * @returns {string|null}
 */
export function toIsoUtc(value) {
  if (!value) return null;
  if (/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value)) return value;
  return `${String(value).replace(' ', 'T')}Z`;
}

function parseJson(value) {
  if (value == null) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null; // a corrupt JSON cell must not take the whole list endpoint down
  }
}

/** Generic reader: parses the JSON columns declared in TABLES[table]. */
export function hydrateRow(table, row) {
  if (!row) return null;
  const out = { ...row };
  for (const [name, spec] of Object.entries(TABLES[table].columns)) {
    if (spec.json && name in out) out[name] = parseJson(out[name]);
  }
  return out;
}

/** Generic writer: serialises JSON columns. */
export function dehydrateRow(table, obj) {
  const out = { ...obj };
  for (const [name, spec] of Object.entries(TABLES[table].columns)) {
    if (spec.json && name in out && out[name] != null && typeof out[name] !== 'string') {
      out[name] = JSON.stringify(out[name]);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Domain objects (what services return) + API DTOs (what React receives)
// ---------------------------------------------------------------------------

/**
 * @typedef {Object} Project
 * @property {number} id
 * @property {string} name
 * @property {string|null} uploadDate  ISO-8601 UTC
 * @property {string[]} languages
 * @property {number|null} filesAnalyzed
 * @property {number|null} healthScore
 * @property {string|null} healthStatus
 *
 * @typedef {Object} Module
 * @property {number} id
 * @property {number} projectId
 * @property {string} fileName
 * @property {string} filePath
 * @property {string} language
 * @property {{total:number, code:number, comments:number, blank:number}|null} loc
 * @property {{total:number, average:number, maximum:number}|null} complexity
 * @property {number|null} riskScore
 * @property {string|null} riskLevel
 *
 * @typedef {Object} MetricSnapshot
 * @property {number} id
 * @property {number} projectId
 * @property {number|null} moduleId
 * @property {string} capturedAt  ISO-8601 UTC
 * @property {Object<string, number|null>} metrics
 */

/** @returns {Project} */
export function projectFromRow(row) {
  return {
    id: row.id,
    name: row.name,
    uploadDate: toIsoUtc(row.upload_date),
    languages: row.languages ? row.languages.split(',').filter(Boolean) : [],
    filesAnalyzed: row.files_analyzed ?? null,
    healthScore: row.health_score ?? null,
    healthStatus: row.health_status ?? null,
  };
}

/**
 * The React app already consumes snake_case JSON from FastAPI
 * (`upload_date`, `health_status`, `files`...). Keeping the wire format
 * identical means ZERO frontend changes. Convert at the edge only: the
 * domain stays idiomatic camelCase JavaScript inside.
 */
export function projectToDto(project) {
  return {
    id: project.id,
    name: project.name,
    upload_date: project.uploadDate,
    health_score: project.healthScore,
    health_status: project.healthStatus,
    files: project.filesAnalyzed,
    languages: project.languages,
  };
}
