/**
 * Central configuration (replaces app/config.py).
 *
 * Node 20.6+ loads .env natively through `node --env-file=.env`, so no
 * dotenv dependency is needed; the npm scripts already pass that flag.
 */
const csv = (value, fallback) =>
  (value ?? fallback)
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

const int = (value, fallback) => {
  const parsed = Number.parseInt(value ?? '', 10);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export const config = Object.freeze({
  appName: 'AI-Assisted Software Metrics Advisor',
  serviceId: 'software-metrics-advisor',
  env: process.env.NODE_ENV ?? 'development',
  host: process.env.HOST ?? '0.0.0.0',
  // 8000 matches the frontend's default VITE_API_BASE_URL, so it works unchanged.
  port: int(process.env.PORT, 8000),
  databasePath: process.env.DATABASE_PATH ?? './software_metrics.db',
  corsOrigins: csv(process.env.CORS_ORIGINS, 'http://localhost:5173,http://localhost:3000'),
  ai: Object.freeze({
    provider: process.env.AI_PROVIDER ?? 'none',
    model: process.env.AI_MODEL ?? 'llama3.1',
    timeoutSeconds: int(process.env.AI_REQUEST_TIMEOUT_SECONDS, 30),
    ollamaBaseUrl: process.env.OLLAMA_BASE_URL ?? 'http://localhost:11434',
  }),
});
