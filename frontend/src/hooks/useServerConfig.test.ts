import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getServerConfig, shutdownServer } from '../api/serverConfig';
import { useServerConfig } from './useServerConfig';

// Model-store API surface the hook touches on shutdown; hoisted so the
// vi.mock factory below can reference it (factories are hoisted above
// module-level consts). useServerConfig calls `useModelStore.getState()`
// (zustand store static), so the mock is a hook-shaped function carrying
// a getState that returns the api object.
const { modelStoreApi, useModelStoreMock } = vi.hoisted(() => {
  const api = {
    syncModelStatus: vi.fn().mockResolvedValue(undefined),
    fetchModels: vi.fn().mockResolvedValue(undefined),
  };
  return {
    modelStoreApi: api,
    useModelStoreMock: Object.assign(vi.fn(() => api), { getState: () => api }),
  };
});

const { tIdentity } = vi.hoisted(() => ({
  tIdentity: (key: string) => key,
}));

// Polling probes resolve immediately; they only keep the hook's state
// machines quiet while the test exercises the shutdown handler.
vi.mock('../api/serverConfig', () => ({
  getServerConfig: vi.fn().mockResolvedValue({
    id: 'default', host: '10.131.1.7', use_auth: false, ssh_port: 22,
    ssh_username: 'uforce', ssh_password: '', ssh_key_path: '', venv_name: '.vllm',
    model_save_path: '', bmc_host: '10.131.1.6', bmc_username: 'uforce',
    bmc_password: '', hf_token: '',
  }),
  getSshStatus: vi.fn().mockResolvedValue({ connected: true, latency_ms: 5 }),
  getServerHealth: vi.fn().mockResolvedValue({ status: 'healthy' }),
  getDashboardHealth: vi.fn().mockResolvedValue({ version: 'test' }),
  getBmcStatus: vi.fn().mockResolvedValue({ configured: true, connected: true, power: 'on', powerFresh: true }),
  shutdownServer: vi.fn(),
  powerOnServer: vi.fn().mockResolvedValue({ status: 'success', powered_on: true }),
  resetModelsAfterPowerOff: vi.fn().mockResolvedValue({ reset: 0 }),
}));

vi.mock('../i18n/context', () => ({
  useI18n: vi.fn(() => ({ t: tIdentity, lang: 'en', toggleLang: vi.fn() })),
}));

vi.mock('../stores/model', () => ({
  useModelStore: useModelStoreMock,
  errMsg: vi.fn((e: unknown) => (e instanceof Error ? e.message : String(e))),
}));

describe('useServerConfig shutdown', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('resets the shutdown state when the shutdown command fails', async () => {
    vi.mocked(shutdownServer).mockRejectedValueOnce(new Error('Failed to connect to SSH'));
    const { result, unmount } = renderHook(() => useServerConfig());

    let failed = false;
    await act(async () => {
      try {
        await result.current.confirmShutdownServer();
      } catch {
        failed = true;
      }
    });

    expect(shutdownServer).toHaveBeenCalledTimes(1);
    expect(failed).toBe(true);
    expect(result.current.shuttingDown).toBe(false);
    unmount();
  });

  it('syncs model status on a successful shutdown', async () => {
    vi.mocked(shutdownServer).mockResolvedValueOnce({ status: 'success', stopped: true });
    const { result, unmount } = renderHook(() => useServerConfig());

    await act(async () => {
      await result.current.confirmShutdownServer();
    });

    expect(modelStoreApi.syncModelStatus).toHaveBeenCalled();
    expect(result.current.shuttingDown).toBe(false);
    unmount();
  });
});

describe('useServerConfig initial-load retry (audit 2026-10-22 FE-11)', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('retries the config load once the API answers (a startup blip must not disable saving forever)', async () => {
    const cfg = vi.mocked(getServerConfig);
    cfg.mockRejectedValueOnce(new Error('network down'));
    const { result, unmount } = renderHook(() => useServerConfig());

    // Flush mount microtasks: the initial load fails while the 2 s API probe
    // succeeds — the retry effect must fire a second load.
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    expect(cfg.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(result.current.serverConfig.host).toBe('10.131.1.7');
    unmount();
  });
});
