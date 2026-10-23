import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import { getWsUrl } from '../api/client';
import { terminalWrite } from '../utils/terminalSink';
import { useConsoleWebSocket } from './useConsoleWebSocket';

vi.mock('../api/client', () => ({
  getWsUrl: vi.fn(async () => 'ws://test/console'),
}));
vi.mock('../stores/console', () => ({
  useConsoleStore: { getState: () => ({ setConnected: vi.fn() }) },
}));
vi.mock('../stores/model', () => ({
  useModelStore: {
    getState: () => ({
      syncModelStatus: () => Promise.resolve(),
      fetchModels: () => Promise.resolve(),
    }),
  },
}));
vi.mock('../utils/terminalSink', () => ({
  terminalReset: vi.fn(),
  terminalSize: vi.fn(() => null),
  terminalWrite: vi.fn(),
}));
vi.mock('../i18n/context', () => ({
  useI18n: () => ({
    lang: 'en',
    t: (key: string) => key,
    setLang: () => {},
    toggleLang: () => {},
  }),
}));

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];
  readyState = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  sent: string[] = [];
  url: string;
  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }
  open() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }
  fail() {
    this.onerror?.();
    this.close();
  }
}

function last(): MockWebSocket {
  return MockWebSocket.instances[MockWebSocket.instances.length - 1];
}

describe('useConsoleWebSocket', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.mocked(getWsUrl).mockReset();
    vi.mocked(getWsUrl).mockResolvedValue('ws://test/console');
    vi.mocked(terminalWrite).mockReset();
    vi.stubGlobal('WebSocket', MockWebSocket);
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('reconnects without an attempt cap (FE-2)', async () => {
    const { result, unmount } = renderHook(() => useConsoleWebSocket());
    await act(async () => {
      result.current.connectConsole();
    });
    expect(MockWebSocket.instances).toHaveLength(1);
    // 12 consecutive failures: the old 10-attempt cap would have given up
    // permanently after the 10th; the socket count must keep growing.
    for (let i = 0; i < 12; i++) {
      await act(async () => {
        last().fail();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(31000);
      });
    }
    expect(MockWebSocket.instances).toHaveLength(13);
    unmount();
  });

  it('caps the reconnect backoff at 30 s', async () => {
    const { result, unmount } = renderHook(() => useConsoleWebSocket());
    await act(async () => {
      result.current.connectConsole();
    });
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        last().fail();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30000);
      });
    }
    // retryCount = 5 → next delay = min(1000 * 2^5, 30000) = 30000 ms.
    const before = MockWebSocket.instances.length;
    await act(async () => {
      last().fail();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(16000);
    });
    expect(MockWebSocket.instances.length).toBe(before);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(MockWebSocket.instances.length).toBe(before + 1);
    unmount();
  });

  it('resets the retry count on a successful open', async () => {
    const { result, unmount } = renderHook(() => useConsoleWebSocket());
    await act(async () => {
      result.current.connectConsole();
    });
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        last().fail();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30000);
      });
    }
    const before = MockWebSocket.instances.length; // 1 + 5
    const ws = last();
    await act(async () => {
      ws.open();
    });
    await act(async () => {
      ws.close();
    });
    // Without the onopen reset the next delay would be 30 s; with it the
    // first backoff step (1 s) applies again.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(MockWebSocket.instances.length).toBe(before + 1);
    unmount();
  });

  it('sends the start command when the start socket opens', async () => {
    const { result, unmount } = renderHook(() => useConsoleWebSocket());
    await act(async () => {
      result.current.startConsoleForModel('vllm serve m1');
    });
    const ws = last();
    await act(async () => {
      ws.open();
    });
    expect(ws.sent).toContainEqual(JSON.stringify({ command: 'vllm serve m1' }));
    unmount();
  });

  it('writes a delivery-failure line to the terminal once when the first start socket fails (FE-8)', async () => {
    const { result, unmount } = renderHook(() => useConsoleWebSocket());
    await act(async () => {
      result.current.startConsoleForModel('vllm serve m1');
    });
    const ws = last();
    expect(ws.sent).toEqual([]);
    await act(async () => {
      ws.fail();
    });
    expect(terminalWrite).toHaveBeenCalledTimes(1);
    expect(vi.mocked(terminalWrite).mock.calls[0][0]).toContain('start command');
    // the reconnect is console mode — a second failure writes no second
    // delivery-failure line
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    await act(async () => {
      last().fail();
    });
    expect(terminalWrite).toHaveBeenCalledTimes(1);
    unmount();
  });

  it('writes a delivery-failure line when the start ticket request fails before any socket (FE-8)', async () => {
    vi.mocked(getWsUrl).mockRejectedValueOnce(new Error('401'));
    const { result, unmount } = renderHook(() => useConsoleWebSocket());
    await act(async () => {
      result.current.startConsoleForModel('vllm serve m1');
    });
    expect(MockWebSocket.instances).toHaveLength(0);
    expect(terminalWrite).toHaveBeenCalledTimes(1);
    // the reconnect is still scheduled (console mode)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(MockWebSocket.instances).toHaveLength(1);
    unmount();
  });

  it('does not open a socket for a connect attempt superseded by unmount (D-3)', async () => {
    // the ticket round-trip is still pending when the hook unmounts:
    // without the seq bump on unmount, `new WebSocket` would still run
    // after unmount and the orphaned socket would leak (D-3).
    let resolveUrl!: (v: string) => void;
    vi.mocked(getWsUrl).mockImplementationOnce(
      () => new Promise<string>((r) => { resolveUrl = r; }),
    );
    const { result, unmount } = renderHook(() => useConsoleWebSocket());
    await act(async () => {
      result.current.connectConsole();
    });
    expect(MockWebSocket.instances).toHaveLength(0); // still awaiting the ticket
    unmount();
    await act(async () => {
      resolveUrl('ws://test/console');
    });
    // the superseded attempt must not create a socket after unmount
    expect(MockWebSocket.instances).toHaveLength(0);
  });
});
