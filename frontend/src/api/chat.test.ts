import { afterEach, describe, expect, it, vi } from 'vitest';
import { openChatStream, parseSseFrame } from './chat';

function streamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

async function drain<T>(gen: AsyncGenerator<T, void, unknown>): Promise<T[]> {
  const out: T[] = [];
  for await (const e of gen) out.push(e);
  return out;
}

describe('parseSseFrame', () => {
  it('parses a content chunk', () => {
    expect(parseSseFrame('data: {"choices":[{"delta":{"content":"Hi"}}]}')).toEqual({
      delta: 'Hi', reasoning: '', usage: null, error: null, done: false,
    });
  });

  it('parses a reasoning chunk (vLLM --reasoning-parser)', () => {
    expect(parseSseFrame('data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}')).toEqual({
      delta: '', reasoning: 'hmm', usage: null, error: null, done: false,
    });
  });

  it('parses a reasoning chunk via delta.reasoning (newer vLLM field name)', () => {
    expect(parseSseFrame('data: {"choices":[{"delta":{"reasoning":"hmm"}}]}')).toEqual({
      delta: '', reasoning: 'hmm', usage: null, error: null, done: false,
    });
  });

  it('parses a usage-only chunk', () => {
    const ev = parseSseFrame('data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":9}}');
    expect(ev?.delta).toBe('');
    expect(ev?.usage).toEqual({ prompt_tokens: 5, completion_tokens: 9 });
    expect(ev?.done).toBe(false);
  });

  it('parses [DONE]', () => {
    expect(parseSseFrame('data: [DONE]')).toEqual({ delta: '', reasoning: '', usage: null, error: null, done: true });
  });

  it('parses an error frame (object and string forms)', () => {
    expect(parseSseFrame('data: {"error":{"message":"boom"}}')?.error).toBe('boom');
    expect(parseSseFrame('data: {"error":"plain failure"}')?.error).toBe('plain failure');
  });

  it('returns null for non-data or malformed frames', () => {
    expect(parseSseFrame(': keep-alive')).toBeNull();
    expect(parseSseFrame('data: {not json')).toBeNull();
  });
});

describe('openChatStream', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('posts the payload and yields events across chunk boundaries', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      body: streamFrom([
        'data: {"choices":[{"delta":{"content":"Hel' + 'lo"}}]}\n\n',
        'data: {"choices":[],"usage":{"completion_tokens":2}}\n\ndata: [DONE]\n\n',
      ]),
    }));
    vi.stubGlobal('fetch', fetchMock);
    const events = await drain(openChatStream('m1', [{ role: 'user', content: 'hi' }], 512, new AbortController().signal));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/v1/models/chat');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      model_id: 'm1', messages: [{ role: 'user', content: 'hi' }], max_tokens: 512,
    });
    // 3 events: content chunk, usage-only chunk, [DONE] (the latter two have empty delta)
    expect(events.map((e) => e.delta)).toEqual(['Hello', '', '']);
    expect(events[1].usage).toEqual({ completion_tokens: 2 });
    expect(events[2].done).toBe(true);
  });

  it('yields a single error event for a pre-stream HTTP failure', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({ detail: 'vLLM API 不可达: timeout' }),
    }));
    vi.stubGlobal('fetch', fetchMock);
    const events = await drain(openChatStream('m1', [{ role: 'user', content: 'hi' }], 512, new AbortController().signal));
    expect(events).toHaveLength(1);
    expect(events[0].error).toBe('vLLM API 不可达: timeout');
    expect(events[0].done).toBe(true);
  });

  it('yields an error event when the fetch itself fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const events = await drain(openChatStream('m1', [{ role: 'user', content: 'hi' }], 512, new AbortController().signal));
    expect(events).toHaveLength(1);
    expect(events[0].error).toBe('Failed to fetch');
  });

  it('sends the stored API key as a header', async () => {
    localStorage.setItem('vllm_api_key', 'sekret');
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, body: streamFrom(['data: [DONE]\n\n']) })));
    const events = await drain(openChatStream('m1', [{ role: 'user', content: 'hi' }], 512, new AbortController().signal));
    expect(events).toHaveLength(1);
    const [, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('sekret');
  });
});
