import { buildApp } from './app.js';
import { config } from './config/env.js';
import { openDatabase } from './database/connection.js';

/**
 * Process entry point: open resources, start listening, and shut down
 * cleanly on SIGINT/SIGTERM (Ctrl-C, Docker stop, systemd, ...).
 */
async function main() {
  const db = openDatabase(config.databasePath);
  const app = await buildApp({ config, db });

  let shuttingDown = false;
  const shutdown = async (signal) => {
    if (shuttingDown) return;
    shuttingDown = true;
    app.log.info({ signal }, 'Shutting down');
    try {
      await app.close(); // stop accepting connections, drain in-flight requests
    } catch (err) {
      app.log.error({ err }, 'Error while closing the HTTP server');
      process.exitCode = 1;
    } finally {
      db.close();
    }
  };
  process.once('SIGINT', () => void shutdown('SIGINT'));
  process.once('SIGTERM', () => void shutdown('SIGTERM'));

  try {
    await app.listen({ host: config.host, port: config.port });
  } catch (err) {
    app.log.error({ err }, 'Failed to start server');
    db.close();
    process.exit(1);
  }
}

// Last-resort nets so a stray rejection never fails silently.
process.on('unhandledRejection', (reason) => {
  console.error('Unhandled promise rejection:', reason);
  process.exitCode = 1;
});

main().catch((err) => {
  console.error('Fatal startup error:', err);
  process.exit(1);
});
