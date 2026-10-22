import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';
import { I18nProvider } from '../i18n';
import { useModelManager } from './useModelManager';
import { useModelStore } from '../stores/model';
import { ModelStatus } from '../types';
import type { ModelInfo } from '../types';
import * as modelsApi from '../api/models';
import { showToast } from '../components/ui/toast';

vi.mock('../components/ui/toast', () => ({
  showToast: vi.fn(),
}));
vi.mock('../api/models', () => ({
  getModels: vi.fn(async () => []),
  startModel: vi.fn(async () => ({ status: 'loading', model_id: 'b', command: 'vllm serve /b' })),
  stopModel: vi.fn(async () => ({ status: 'success', stopped: true })),
  syncModelStatus: vi.fn(async () => ({ synced: true, synced_count: 0 })),
  scanModels: vi.fn(async () => ({ count: 0 })),
  getLaunchConfig: vi.fn(async () => ({ config: null })),
  saveLaunchConfig: vi.fn(async () => { }),
  deleteModel: vi.fn(async () => { }),
  setModelLock: vi.fn(async () => ({ model_id: 'm', locked: true, unlocked: [] })),
}));

const mk = (id: string, name: string, status: ModelInfo['status']): ModelInfo =>
  ({ id, name, path: `/models/${id}`, status, size_bytes: 0, locked: false });

function renderManager() {
  const startConsoleForModel = vi.fn();
  const utils = renderHook(
    () => useModelManager({ startConsoleForModel, sshConnected: false, connectConsole: vi.fn() }),
    { wrapper: I18nProvider },
  );
  return { ...utils, startConsoleForModel };
}

describe('useModelManager model switch (start while another runs)', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('asks before starting while another model is running or loading', async () => {
    const { result } = renderManager();
    useModelStore.setState({ models: [mk('a', 'Alpha', ModelStatus.LOADING), mk('b', 'Beta', ModelStatus.STOPPED)] });
    await act(async () => { await result.current.handleStartModel('b'); });
    expect(modelsApi.startModel).not.toHaveBeenCalled();
    expect(result.current.startRequest).toEqual({ modelId: 'b', name: 'Beta', stopId: 'a', stopName: 'Alpha' });
  });

  it('confirm stops the running model first, then starts the target', async () => {
    const { result, startConsoleForModel } = renderManager();
    useModelStore.setState({ models: [mk('a', 'Alpha', ModelStatus.RUNNING), mk('b', 'Beta', ModelStatus.STOPPED)] });
    await act(async () => { await result.current.handleStartModel('b'); });
    expect(result.current.startRequest).not.toBeNull();
    await act(async () => { await result.current.confirmStart(); });
    expect(modelsApi.stopModel).toHaveBeenCalledTimes(1);
    expect(modelsApi.stopModel).toHaveBeenCalledWith('a');
    expect(modelsApi.startModel).toHaveBeenCalledTimes(1);
    expect(modelsApi.startModel).toHaveBeenCalledWith('b');
    expect(startConsoleForModel).toHaveBeenCalledWith('vllm serve /b');
    expect(result.current.startRequest).toBeNull();
  });

  it('does not start the second model when the stop did not confirm', async () => {
    vi.mocked(modelsApi.stopModel).mockResolvedValueOnce({ status: 'timeout', stopped: false });
    const { result } = renderManager();
    useModelStore.setState({ models: [mk('a', 'Alpha', ModelStatus.RUNNING), mk('b', 'Beta', ModelStatus.STOPPED)] });
    await act(async () => { await result.current.handleStartModel('b'); });
    await act(async () => { await result.current.confirmStart(); });
    expect(modelsApi.stopModel).toHaveBeenCalledTimes(1);
    expect(modelsApi.startModel).not.toHaveBeenCalled();
    expect(result.current.startRequest).toBeNull();
    // FE-6: the skipped start is announced (the last toast), not silently dropped.
    const calls = vi.mocked(showToast).mock.calls;
    expect(calls[calls.length - 1]?.[0]).toContain('was skipped');
  });

  it('toasts when opening the launch config fails (FE-5)', async () => {
    vi.mocked(modelsApi.getLaunchConfig).mockRejectedValueOnce(new Error('boom'));
    const { result } = renderManager();
    await act(async () => {
      await result.current.handleOpenLaunchConfig('b', 'Beta', '/models/b');
    });
    expect(result.current.showLaunchConfigModal).toBe(false);
    const toastCalls = vi.mocked(showToast).mock.calls;
    expect(toastCalls[toastCalls.length - 1]?.[0]).toContain('boom');
  });

  it('starts directly when nothing else is running or loading', async () => {
    const { result } = renderManager();
    useModelStore.setState({ models: [mk('b', 'Beta', ModelStatus.STOPPED)] });
    await act(async () => { await result.current.handleStartModel('b'); });
    expect(result.current.startRequest).toBeNull();
    expect(modelsApi.startModel).toHaveBeenCalledTimes(1);
    expect(modelsApi.startModel).toHaveBeenCalledWith('b');
  });

  it('cancel clears the request without stopping or starting', async () => {
    const { result } = renderManager();
    useModelStore.setState({ models: [mk('a', 'Alpha', ModelStatus.RUNNING), mk('b', 'Beta', ModelStatus.STOPPED)] });
    await act(async () => { await result.current.handleStartModel('b'); });
    await act(async () => { await result.current.cancelStart(); });
    expect(result.current.startRequest).toBeNull();
    expect(modelsApi.stopModel).not.toHaveBeenCalled();
    expect(modelsApi.startModel).not.toHaveBeenCalled();
  });
});
