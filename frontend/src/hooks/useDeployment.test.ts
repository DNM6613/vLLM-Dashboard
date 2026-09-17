import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api/deployment';
import type { CudaInfo, DeployState, DriverList, Preflight, VllmInfo } from '../api/deployment';
import { tRaw } from '../i18n/strings';
import { useDeployment } from './useDeployment';

vi.mock('../components/ui/toast', () => ({ showToast: vi.fn() }));

vi.mock('../api/deployment', () => ({
  apiErrorMessage: (e: unknown) => (e instanceof Error ? e.message : String(e)),
  applyCuda: vi.fn(),
  applyDriver: vi.fn(),
  applyVllm: vi.fn(),
  exportEnv: vi.fn(),
  getCudaInfo: vi.fn(),
  getDeployState: vi.fn(),
  getDrivers: vi.fn(),
  getPreflight: vi.fn(),
  getTemplates: vi.fn(async () => []),
  getVllmInfo: vi.fn(),
  importEnv: vi.fn(),
  rebootServer: vi.fn(),
  rollbackEnv: vi.fn(),
  scanConflicts: vi.fn(),
  updateDeployState: vi.fn(),
}));

const PREFLIGHT = {
  ok: true,
  blocking: false,
  items: [],
  checked_at: 'now',
} as unknown as Preflight;
const DRIVERS = {
  gpu_models: [],
  drivers: [],
  current_driver: '',
  current_major: null,
  min_driver: 0,
  driver_min_cuda: {},
  pending_task: null,
} as unknown as DriverList;
const CUDA = {
  versions: [],
  min_driver: {},
  selected: '',
  install_system: false,
  driver_major: null,
  current_toolkit: '',
  installed_toolkit: '',
} as unknown as CudaInfo;
const VLLM = {
  uv: '',
  uv_missing: false,
  pythons: [],
  venvs: [],
  vllm_version: '',
  venv_name: '',
  cuda_versions: [],
  selected: { version: '', runtime: '', cuda: '' },
  snapshot: { version: '', previous_version: '', venv: '' },
} as unknown as VllmInfo;
const STATE = {
  mirrors: { pypi: '', hf: '' },
  selected: { driver: '', cuda: '', cuda_install_system: false, vllm_version: '', vllm_runtime: '' },
  snapshots: {
    driver: { current_pkg: '', previous_pkg: '', previous_version: '' },
    cuda: { installed_version: '', toolkit_path: '' },
    vllm: { version: '', previous_version: '', venv: '' },
  },
  history: [],
  current_driver_major: null,
  pending_driver_task: null,
  locks: { cuda: false, vllm: false },
} as unknown as DeployState;

function stubAllGetters() {
  vi.mocked(api.getPreflight).mockResolvedValue(PREFLIGHT);
  vi.mocked(api.getDrivers).mockResolvedValue(DRIVERS);
  vi.mocked(api.getDeployState).mockResolvedValue(STATE);
  vi.mocked(api.getCudaInfo).mockResolvedValue(CUDA);
  vi.mocked(api.getVllmInfo).mockResolvedValue(VLLM);
}

describe('useDeployment.loadAll', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders every successful probe even when one endpoint fails', async () => {
    stubAllGetters();
    vi.mocked(api.getPreflight).mockRejectedValueOnce(new Error('timeout'));
    const { result } = renderHook(() => useDeployment(true));
    await waitFor(() => expect(result.current.preflightLoading).toBe(false));
    expect(result.current.drivers).toBe(DRIVERS);
    expect(result.current.cuda).toBe(CUDA);
    expect(result.current.vllm).toBe(VLLM);
    expect(result.current.state).toBe(STATE);
    expect(result.current.preflight).toBeNull();
    expect(result.current.error).toBe(
      tRaw('Partial load failed ({n} of 5) — retry with Re-check.').replace('{n}', '1'),
    );
  });

  it('clears the error when every probe succeeds', async () => {
    stubAllGetters();
    const { result } = renderHook(() => useDeployment(true));
    await waitFor(() => expect(result.current.preflightLoading).toBe(false));
    expect(result.current.preflight).toBe(PREFLIGHT);
    expect(result.current.error).toBe('');
  });

  it('skips a poll tick while the previous round is still in flight', async () => {
    vi.useFakeTimers();
    try {
      const never = () => new Promise<never>(() => undefined);
      vi.mocked(api.getPreflight).mockImplementation(never);
      vi.mocked(api.getDrivers).mockImplementation(never);
      vi.mocked(api.getDeployState).mockImplementation(never);
      vi.mocked(api.getCudaInfo).mockImplementation(never);
      vi.mocked(api.getVllmInfo).mockImplementation(never);

      renderHook(() => useDeployment(true));
      // initial loadAll issues one call per getter
      expect(vi.mocked(api.getDrivers)).toHaveBeenCalledTimes(1);

      await act(async () => {
        vi.advanceTimersByTime(5000);
      });
      // first tick fires a fresh round
      expect(vi.mocked(api.getDrivers)).toHaveBeenCalledTimes(2);

      await act(async () => {
        vi.advanceTimersByTime(5000);
      });
      // second tick lands while round 1 never settles -> skipped
      expect(vi.mocked(api.getDrivers)).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
