import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import { openChatStream } from '../api/chat';
import type { ChatStreamEvent } from '../api/chat';
import { useChat } from './useChat';

vi.mock('../api/chat', () => ({
  CHAT_MAX_TOKENS: 4096,
  openChatStream: vi.fn(),
}));

const mockedStream = vi.mocked(openChatStream);

async function* gen(events: ChatStreamEvent[]): AsyncGenerator<ChatStreamEvent, void, unknown> {
  for (const e of events) yield e;
}

describe('useChat', () => {
  beforeEach(() => {
    mockedStream.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('streams deltas into the assistant entry and records usage', async () => {
    mockedStream.mockImplementation(() => gen([
      { delta: 'Hel', reasoning: '', usage: null, error: null, done: false },
      { delta: 'lo', reasoning: '', usage: null, error: null, done: false },
      { delta: '', reasoning: '', usage: { completion_tokens: 2 }, error: null, done: true },
    ]));
    const { result } = renderHook(() => useChat());
    let p: Promise<void>;
    await act(async () => {
      p = result.current.send('m1', 'hi');
    });
    expect(result.current.entries).toHaveLength(2);
    expect(result.current.entries[0]).toMatchObject({ role: 'user', content: 'hi' });
    await act(async () => {
      await p!;
    });
    expect(result.current.streaming).toBe(false);
    const asst = result.current.entries[1];
    expect(asst.content).toBe('Hello');
    expect(asst.streaming).toBe(false);
    expect(asst.usage).toEqual({ completion_tokens: 2 });
    expect(asst.elapsedMs).not.toBeNull();
    expect(mockedStream).toHaveBeenCalledTimes(1);
    expect(mockedStream.mock.calls[0][0]).toBe('m1');
    expect(mockedStream.mock.calls[0][1]).toEqual([{ role: 'user', content: 'hi' }]);
  });

  it('accumulates reasoning content separately from the answer', async () => {
    mockedStream.mockImplementation(() => gen([
      { delta: '', reasoning: 'step one; ', usage: null, error: null, done: false },
      { delta: '', reasoning: 'step two', usage: null, error: null, done: false },
      { delta: 'answer', reasoning: '', usage: null, error: null, done: true },
    ]));
    const { result } = renderHook(() => useChat());
    let p: Promise<void>;
    await act(async () => {
      p = result.current.send('m1', 'hi');
    });
    await act(async () => {
      await p!;
    });
    const asst = result.current.entries[1];
    expect(asst.reasoning).toBe('step one; step two');
    expect(asst.content).toBe('answer');
  });

  it('queues a message while streaming and auto-sends it after the first ends', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((res) => { release = res; });
    let call = 0;
    mockedStream.mockImplementation(() => {
      call += 1;
      if (call === 1) {
        return (async function* () {
          yield { delta: 'a1', reasoning: '', usage: null, error: null, done: false };
          await gate;
          yield { delta: '', reasoning: '', usage: null, error: null, done: true };
        })();
      }
      return gen([
        { delta: 'a2', reasoning: '', usage: null, error: null, done: true },
      ]);
    });
    const { result } = renderHook(() => useChat());
    let p: Promise<void>;
    await act(async () => {
      p = result.current.send('m1', 'first');
      await Promise.resolve();
    });
    expect(result.current.streaming).toBe(true);
    await act(async () => {
      void result.current.send('m1', 'second');
    });
    expect(result.current.queue).toEqual(['second']);
    await act(async () => {
      result.current.editQueued(0, 'second (edited)');
    });
    expect(result.current.queue).toEqual(['second (edited)']);
    release();
    await act(async () => {
      await p!;
    });
    expect(mockedStream).toHaveBeenCalledTimes(2);
    expect(result.current.queue).toEqual([]);
    expect(result.current.entries.map((e) => e.content)).toEqual(['first', 'a1', 'second (edited)', 'a2']);
    expect(result.current.streaming).toBe(false);
  });

  it('removeQueued drops a queued message without sending it', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((res) => { release = res; });
    mockedStream.mockImplementation(() => (async function* () {
      yield { delta: 'a1', reasoning: '', usage: null, error: null, done: false };
      await gate;
      yield { delta: '', reasoning: '', usage: null, error: null, done: true };
    })());
    const { result } = renderHook(() => useChat());
    let p: Promise<void>;
    await act(async () => {
      p = result.current.send('m1', 'first');
      await Promise.resolve();
    });
    await act(async () => {
      void result.current.send('m1', 'second');
    });
    expect(result.current.queue).toEqual(['second']);
    await act(async () => {
      result.current.removeQueued(0);
    });
    expect(result.current.queue).toEqual([]);
    release();
    await act(async () => {
      await p!;
    });
    expect(mockedStream).toHaveBeenCalledTimes(1);
    expect(result.current.entries.map((e) => e.content)).toEqual(['first', 'a1']);
  });

  it('surfaces a backend error frame on the assistant entry', async () => {
    mockedStream.mockImplementation(() => gen([
      { delta: '', reasoning: '', usage: null, error: 'vLLM API 不可达: timeout', done: true },
    ]));
    const { result } = renderHook(() => useChat());
    let p: Promise<void>;
    await act(async () => {
      p = result.current.send('m1', 'hi');
    });
    await act(async () => {
      await p!;
    });
    expect(result.current.streaming).toBe(false);
    expect(result.current.entries[1].error).toBe('vLLM API 不可达: timeout');
  });

  it('includes previous turns in the messages of the next request', async () => {
    mockedStream.mockImplementation(() => gen([
      { delta: 'x', reasoning: '', usage: null, error: null, done: true },
    ]));
    const { result } = renderHook(() => useChat());
    let p: Promise<void>;
    await act(async () => {
      p = result.current.send('m1', 'first');
    });
    await act(async () => {
      await p!;
    });
    let p2: Promise<void>;
    await act(async () => {
      p2 = result.current.send('m1', 'second');
    });
    await act(async () => {
      await p2!;
    });
    expect(mockedStream).toHaveBeenCalledTimes(2);
    const secondCall = mockedStream.mock.calls[1] as [string, { role: string; content: string }[], number, AbortSignal];
    expect(secondCall[1]).toEqual([
      { role: 'user', content: 'first' },
      { role: 'assistant', content: 'x' },
      { role: 'user', content: 'second' },
    ]);
  });

  it('stop() aborts an in-flight stream and keeps partial output', async () => {
    let signal: AbortSignal | undefined;
    mockedStream.mockImplementation((_id, _msgs, _max, sig) => {
      signal = sig;
      return (async function* () {
        yield { delta: 'a', reasoning: '', usage: null, error: null, done: false };
        await new Promise((_res, rej) => {
          sig.addEventListener('abort', () => rej(new DOMException('Aborted', 'AbortError')), { once: true });
        });
      })();
    });
    const { result } = renderHook(() => useChat());
    let p: Promise<void>;
    await act(async () => {
      p = result.current.send('m1', 'hi');
      // let the first delta land
      await Promise.resolve();
    });
    expect(result.current.streaming).toBe(true);
    expect(result.current.entries[1].content).toBe('a');
    await act(async () => {
      result.current.stop();
      await p!;
    });
    expect(result.current.streaming).toBe(false);
    expect(result.current.entries[1].content).toBe('a');
    expect(result.current.entries[1].error).toBeNull();
    expect(signal?.aborted).toBe(true);
  });

  it('clear() empties the entries', async () => {
    mockedStream.mockImplementation(() => gen([
      { delta: 'x', reasoning: '', usage: null, error: null, done: true },
    ]));
    const { result } = renderHook(() => useChat());
    let p: Promise<void>;
    await act(async () => {
      p = result.current.send('m1', 'hi');
    });
    await act(async () => {
      await p!;
    });
    expect(result.current.entries).toHaveLength(2);
    await act(async () => {
      result.current.clear();
    });
    expect(result.current.entries).toHaveLength(0);
  });
});
