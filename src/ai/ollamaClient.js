/**
 * Minimal Ollama client over plain fetch (Node 20 has it built in).
 *
 * Why not an SDK? There is no package named `@ollama/sdk`; the official
 * client is `ollama` on npm. For a single non-streaming chat call, fetch is
 * a dependency-free equivalent. Swap in the SDK later if you need streaming
 * helpers: only this file changes.
 *
 * Contract (same as the Python advisor): never throw to the caller. If the
 * provider is disabled or unreachable the API answers `available: false`
 * instead of failing the request.
 */
export async function generateWithOllama({ baseUrl, model, timeoutSeconds, system, prompt }) {
  try {
    const response = await fetch(`${baseUrl}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        stream: false,
        format: 'json',
        messages: [
          { role: 'system', content: system },
          { role: 'user', content: prompt },
        ],
      }),
      signal: AbortSignal.timeout(timeoutSeconds * 1000),
    });
    if (!response.ok) {
      return { available: false, text: null, message: `AI provider returned HTTP ${response.status}.` };
    }
    const data = await response.json();
    return { available: true, text: data?.message?.content ?? '', message: null };
  } catch (err) {
    const timedOut = err?.name === 'TimeoutError';
    return {
      available: false,
      text: null,
      message: timedOut ? 'AI provider timed out.' : 'AI provider is unreachable.',
    };
  }
}
