export interface AppConfig {
  readonly appName: string;
  readonly serviceId: string;
  readonly env: string;
  readonly host: string;
  readonly port: number;
  readonly useMockDb: boolean;
  readonly corsOrigins: readonly string[];
  readonly ai: {
    readonly model: string;
    readonly timeoutSeconds: number;
    readonly ollamaBaseUrl: string;
  };
}

const csv = (value: string | undefined, fallback: string): string[] =>
  (value ?? fallback)
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

const int = (value: string | undefined, fallback: number): number => {
  const parsed = Number.parseInt(value ?? '', 10);
  return Number.isFinite(parsed) ? parsed : fallback;
};

/**
 * Node 20.6+ loads .env natively via `node --env-file=.env`, so no dotenv
 * dependency. DATABASE_URL is read by Prisma itself (see schema.prisma).
 */
export function loadConfig(env: NodeJS.ProcessEnv = process.env): AppConfig {
  return Object.freeze({
    appName: 'AI-Assisted Software Metrics Advisor',
    serviceId: 'software-metrics-advisor',
    env: env['NODE_ENV'] ?? 'development',
    host: env['HOST'] ?? '0.0.0.0',
    port: int(env['PORT'], 8000), // matches the frontend's default API base URL
    useMockDb: env['USE_MOCK_DB'] === 'true',
    corsOrigins: csv(env['CORS_ORIGINS'], 'http://localhost:5173,http://localhost:3000'),
    ai: Object.freeze({
      model: env['AI_MODEL'] ?? 'llama3.1',
      timeoutSeconds: int(env['AI_REQUEST_TIMEOUT_SECONDS'], 30),
      ollamaBaseUrl: env['OLLAMA_BASE_URL'] ?? 'http://localhost:11434',
    }),
  });
}
