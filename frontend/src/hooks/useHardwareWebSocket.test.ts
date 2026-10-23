import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getWsUrl } from '../api/client';
import { useHardwareWebSocket } from './useHardwareWebSocket';

vi.mock('../api/client', () => ({
  getWsUrl: vi.fn(),
}));

vi.mock('../stores/hardware', () => ({
  useHardwareStore: { getState: () => ({ addMetrics: vi.fn() }) },
}));

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  close = vi.fn();
  onopen: (() => void) | null = null;
  onmessage: ((e: unknown) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
}

const flushMicrotasks = () => new Promise((r) => setTimeout(r, 0));

// Audit 2026-10-23 D-4: getWsUrl is an await gap — a socket minted AFTER the
// effect cleaned up (unmount) must be closed immediately, never stored.
describe('useHardwareWebSocket unmount during the async url mint (D-4)', () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.mocked(getWsUrl).mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('closes a socket minted after the unmount (never stored)', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    let resolveUrl!: (u: string) => void;
    vi.mocked(getWsUrl).mockReturnValue(
      new Promise<string>((r) => { resolveUrl = r; }),
    );
    const { unmount } = renderHook(() => useHardwareWebSocket());
    // getWsUrl is still pending — no socket yet. Unmount now.
    unmount();
    await act(async () => { resolveUrl('ws://x/ws/hardware'); });
    await act(async () => { await flushMicrotasks(); });
    expect(FakeWebSocket.instances).toHaveLength(1);
    // The post-cleanup mint was closed at once, not handed to the reconnect loop.
    expect(FakeWebSocket.instances[0].close).toHaveBeenCalledTimes(1);
  });

  it('keeps a socket minted while still mounted (unmount closes it once)', async () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    let resolveUrl!: (u: string) => void;
    vi.mocked(getWsUrl).mockReturnValue(
      new Promise<string>((r) => { resolveUrl = r; }),
    );
    const { unmount } = renderHook(() => useHardwareWebSocket());
    await act(async () => { resolveUrl('ws://x/ws/hardware'); });
    await act(async () => { await flushMicrotasks(); });
    // Mounted at mint time: nothing may close it early.
    expect(FakeWebSocket.instances[0].close).not.toHaveBeenCalled();
    unmount();
    expect(FakeWebSocket.instances[0].close).toHaveBeenCalledTimes(1);
  });
});
