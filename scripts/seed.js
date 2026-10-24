/**
 * Inserts two demo projects (+ a snapshot) so GET /api/projects returns data
 * before the upload pipeline is ported.   Usage: npm run seed
 */
import { config } from '../src/config/env.js';
import { openDatabase } from '../src/database/connection.js';

const db = openDatabase(config.databasePath);
try {
  const insertProject = db.prepare(
    `INSERT INTO projects (name, upload_date, status, languages, files_analyzed, total_classes,
                           total_functions, total_loc, health_score, health_status, category_scores)
     VALUES (@name, @upload_date, 'analyzed', @languages, @files, @classes, @functions, @loc,
             @health_score, @health_status, @category_scores)`,
  );
  const insertSnapshot = db.prepare(
    `INSERT INTO metric_snapshots (project_id, module_id, captured_at, loc_total, complexity_total, health_score)
     VALUES (?, NULL, ?, ?, ?, ?)`,
  );

  const seed = db.transaction(() => {
    const now = new Date().toISOString();
    const demo = [
      { name: 'demo_python', languages: 'python', files: 3, classes: 4, functions: 14, loc: 179, health_score: 78.5, health_status: 'HEALTHY' },
      { name: 'demo_java', languages: 'java', files: 3, classes: 3, functions: 11, loc: 136, health_score: 61.2, health_status: 'NEEDS_ATTENTION' },
    ];
    for (const p of demo) {
      const { lastInsertRowid } = insertProject.run({
        ...p,
        upload_date: now,
        category_scores: JSON.stringify({ complexity: 80, maintainability: 70 }),
      });
      insertSnapshot.run(lastInsertRowid, now, p.loc, p.functions * 2, p.health_score);
    }
  });
  seed();
  console.log(`Seeded demo projects into ${config.databasePath}`);
} finally {
  db.close();
}
