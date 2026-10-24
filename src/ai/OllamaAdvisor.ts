import { Ollama } from 'ollama';

/**
 * AI advisor over a local Ollama instance (replaces app/ai/advisor.py).
 *
 * NOTE ON THE PACKAGE NAME: there is no `@ollama/sdk` on npm. The official
 * client is `ollama` (https://github.com/ollama/ollama-js), used here.
 *
 * Contract (same as the Python advisor): `recommend` NEVER throws. If Ollama
 * is down, slow, or answers with malformed JSON the result is
 * `{ available: false, ... }` and the rest of the API is unaffected. The model
 * only explains already-computed metrics; it never recalculates anything.
 */

export interface AdvisorContext {
  projectName: string;
  healthScore: number | null;
  healthStatus: string | null;
  riskyModules: readonly { fileName: string; riskScore: number | null; riskFactors: readonly string[] }[];
}

export interface AdvisorRecommendation {
  problem: string;
  severity: 'low' | 'medium' | 'high';
  explanation: string;
  recommendation: string;
  priority: number;
}

export interface AdvisorResult {
  available: boolean;
  recommendations: AdvisorRecommendation[];
  message: string | null;
}

export interface OllamaAdvisorOptions {
  baseUrl: string;
  model: string;
  timeoutSeconds: number;
}

const SYSTEM_PROMPT =
  'You are a software quality advisor. You are given metrics that were ALREADY computed. ' +
  'Explain them and suggest practical refactorings. Never recompute or invent metrics. ' +
  'Answer ONLY with JSON: {"recommendations":[{"problem":"","severity":"low|medium|high",' +
  '"explanation":"","recommendation":"","priority":1}]}';

const SEVERITIES: readonly string[] = ['low', 'medium', 'high'];

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

/** Type guard: lets us accept model output without a single cast. */
function isRecommendation(v: unknown): v is AdvisorRecommendation {
  return (
    isRecord(v) &&
    typeof v['problem'] === 'string' &&
    typeof v['severity'] === 'string' &&
    SEVERITIES.includes(v['severity']) &&
    typeof v['explanation'] === 'string' &&
    typeof v['recommendation'] === 'string' &&
    typeof v['priority'] === 'number'
  );
}

const unavailable = (message: string): AdvisorResult => ({ available: false, recommendations: [], message });

export class OllamaAdvisor {
  private readonly client: Ollama;
  private readonly model: string;
  private readonly timeoutMs: number;

  constructor(options: OllamaAdvisorOptions) {
    this.client = new Ollama({ host: options.baseUrl });
    this.model = options.model;
    this.timeoutMs = options.timeoutSeconds * 1000;
  }

  async recommend(context: AdvisorContext): Promise<AdvisorResult> {
    let timer: NodeJS.Timeout | undefined;
    try {
      const timeout = new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          this.client.abort(); // cancel the in-flight HTTP request
          reject(new Error('AI provider timed out.'));
        }, this.timeoutMs);
      });
      const response = await Promise.race([
        this.client.chat({
          model: this.model,
          stream: false,
          format: 'json',
          messages: [
            { role: 'system', content: SYSTEM_PROMPT },
            { role: 'user', content: JSON.stringify(context) },
          ],
        }),
        timeout,
      ]);

      const parsed: unknown = JSON.parse(response.message.content);
      const items = isRecord(parsed) ? parsed['recommendations'] : undefined;
      if (!Array.isArray(items)) return unavailable('AI provider returned an unexpected response.');

      const recommendations = items.filter(isRecommendation).sort((a, b) => a.priority - b.priority);
      return { available: true, recommendations, message: null };
    } catch (err) {
      const timedOut = err instanceof Error && err.message === 'AI provider timed out.';
      return unavailable(timedOut ? 'AI provider timed out.' : 'AI provider is unreachable or returned invalid data.');
    } finally {
      clearTimeout(timer);
    }
  }
}
