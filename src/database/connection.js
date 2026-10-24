import Database from 'better-sqlite3';
import { SCHEMA_SQL } from './schema.js';

/**
 * Opens (and if needed creates) the SQLite database.
 *
 * Replaces app/database/database.py. Differences worth knowing:
 *  - better-sqlite3 is SYNCHRONOUS by design and is the fastest Node SQLite
 *    driver; there is no session/`Depends(get_db)` per request. One shared
 *    connection is safe because Node runs JS on a single thread. Repositories
 *    still expose async methods so swapping in Prisma or a pooled driver
 *    later never changes a single caller.
 *  - WAL mode lets reads proceed while a scan is writing results.
 */
export function openDatabase(path) {
  const db = new Database(path);
  try {
    db.pragma('journal_mode = WAL');
    db.pragma('foreign_keys = ON');
    db.exec(SCHEMA_SQL);
    return db;
  } catch (err) {
    db.close(); // never leak the file handle when initialisation fails
    throw err;
  }
}
