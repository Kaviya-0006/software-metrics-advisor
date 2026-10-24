import { buildApp } from './app.js';
import { loadConfig } from './config/env.js';
import { openDatabase } from './database/connection.js';

/** Process entry point: open resources, listen, shut down cleanly on signals. */
async function main(): Promise<void> {
  const config = loadConfig();
  const database = await openDatabase(config);
  const app = await buildApp({ config, db: database.client });

  let shuttingDown = false;
  const shutdown = async (signal: string): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    app.log.info({ signal }, 'Shutting down');
    try {
      await app.close(); // stop accepting connections, drain in-flight requests
    } catch (err) {
      app.log.error({ err }, 'Error while closing the HTTP server');
      process.exitCode = 1;
    } finally {
      await database.close();
    }
  };
  process.once('SIGINT', () => void shutdown('SIGINT'));
  process.once('SIGTERM', () => void shutdown('SIGTERM'));

  try {
    await app.listen({ host: config.host, port: config.port });
    if (config.useMockDb) app.log.warn('USE_MOCK_DB=true: serving built-in sample data');
  } catch (err) {
    app.log.error({ err }, 'Failed to start server');
    await database.close();
    process.exit(1);
  }
}

// Last-resort nets so a stray rejection never fails silently.
process.on('unhandledRejection', (reason: unknown) => {
  console.error('Unhandled promise rejection:', reason);
  process.exitCode = 1;
});

main().catch((err: unknown) => {
  console.error('Fatal startup error:', err);
  process.exit(1);
});
