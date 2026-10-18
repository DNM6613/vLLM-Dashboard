import { getStoredApiKey } from '../utils/apiKey';

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface ChatUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface ChatStreamEvent {
  delta: string;
  reasoning: string;
  usage: ChatUsage | null;
  error: string | null;
  done: boolean;
}

// v1 default: generous enough for thinking models (thinking tokens share the
// generation budget); a parameter panel may come later.
export const CHAT_MAX_TOKENS = 4096;

/** Parse one SSE frame (``data: ...``); null when it carries no payload. */
export function parseSseFrame(frame: string): ChatStreamEvent | null {
  const dataLine = frame.split('\n').find((l) => l.startsWith('data:'));
  if (dataLine === undefined) return null;
  const data = dataLine.slice(5).trim();
  if (data === '[DONE]') return { delta: '', reasoning: '', usage: null, error: null, done: true };
  try {
    const j = JSON.parse(data) as {
      error?: unknown;
      choices?: { delta?: { content?: unknown; reasoning_content?: unknown; reasoning?: unknown }; text?: string }[];
      usage?: ChatUsage;
    };
    if (j.error) {
      const msg = typeof j.error === 'string'
        ? j.error
        : (j.error as { message?: unknown }).message ?? JSON.stringify(j.error);
      return { delta: '', reasoning: '', usage: null, error: String(msg), done: true };
    }
    let delta = '';
    let reasoning = '';
    const choice = Array.isArray(j.choices) ? j.choices[0] : undefined;
    if (choice?.delta?.content != null) delta = String(choice.delta.content);
    else if (typeof choice?.text === 'string') delta = choice.text;
    // vLLM emits thinking as a separate field only when launched with
    // --reasoning-parser; otherwise it is baked into content and absent here.
    // Newer vLLM versions use delta.reasoning; the OpenAI-compatible name is
    // delta.reasoning_content — accept both (verified live 2026-10-19).
    const thinking = choice?.delta?.reasoning_content ?? choice?.delta?.reasoning;
    if (thinking != null) reasoning = String(thinking);
    const usage = j.usage && typeof j.usage === 'object' ? j.usage : null;
    return { delta, reasoning, usage, error: null, done: false };
  } catch {
    return null;
  }
}

/**
 * POST to the backend chat proxy and yield parsed SSE events. The response
 * is a verbatim passthrough of vLLM's ``/v1/chat/completions`` stream;
 * frames are delimited by blank lines. Failures before the stream starts
 * arrive as the backend's JSON error envelope (``{detail}``); failures
 * mid-stream arrive as ``{"error": ...}`` frames.
 */
export async function* openChatStream(
  modelId: string,
  messages: ChatMessage[],
  maxTokens: number,
  signal: AbortSignal,
): AsyncGenerator<ChatStreamEvent, void, unknown> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const key = getStoredApiKey();
  if (key) headers['X-API-Key'] = key;

  let res: Response;
  try {
    res = await fetch('/api/v1/models/chat', {
      method: 'POST',
      headers,
      body: JSON.stringify({ model_id: modelId, messages, max_tokens: maxTokens }),
      signal,
    });
  } catch (e) {
    if (e instanceof Error && e.name === 'AbortError') return;
    yield { delta: '', reasoning: '', usage: null, error: e instanceof Error ? e.message : String(e), done: true };
    return;
  }

  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === 'string') detail = j.detail;
      else if (j.detail && typeof j.detail === 'object') detail = JSON.stringify(j.detail);
    } catch {
      // keep the HTTP status as the detail
    }
    yield { delta: '', reasoning: '', usage: null, error: detail, done: true };
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const ev = parseSseFrame(frame);
        if (ev) yield ev;
      }
    }
    const tail = buf.trim();
    if (tail) {
      const ev = parseSseFrame(tail);
      if (ev) yield ev;
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // the lock may already be released after an abort
    }
  }
}
