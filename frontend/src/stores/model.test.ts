import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getModels } from '../api/models';
import { ModelStatus } from '../types';
import type { ModelInfo } from '../types';
import { useModelStore } from './model';

vi.mock('../api/models', () => ({
  getModels: vi.fn(),
  syncModelStatus: vi.fn(),
  stopModel: vi.fn(),
  startModel: vi.fn(),
  setModelLock: vi.fn(),
  scanModels: vi.fn(),
  getLaunchConfig: vi.fn(),
  saveLaunchConfig: vi.fn(),
  deleteModel: vi.fn(),
  benchmarkModel: vi.fn(),
  downloadModel: vi.fn(),
  getDownloadStatus: vi.fn(),
  stopDownload: vi.fn(),
  checkCliTools: vi.fn(),
  installCliTool: vi.fn(),
  getInstallStatus: vi.fn(),
}));

const model: ModelInfo = {
  id: 'm1', name: 'Model One', path: '/models/m1',
  status: ModelStatus.STOPPED, size_bytes: 0, locked: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  useModelStore.setState({ models: [], loading: false, error: null });
});

// Audit 2026-10-23 D-10: a background refresh (polls, post-download,
// post-shutdown sync) must not flash the whole-page loading state — only
// the FIRST fetch (empty list) shows the spinner.
describe('useModelStore fetchModels loading flash (D-10)', () => {
  it('shows the spinner while the first (empty-list) fetch is in flight', async () => {
    let release!: (v: ModelInfo[]) => void;
    vi.mocked(getModels).mockImplementation(
      () => new Promise<ModelInfo[]>((r) => { release = r; }),
    );
    const pending = useModelStore.getState().fetchModels();
    // set() runs synchronously before the first await — spinner up now.
    expect(useModelStore.getState().loading).toBe(true);
    release([model]);
    await pending;
    expect(useModelStore.getState().loading).toBe(false);
    expect(useModelStore.getState().models).toHaveLength(1);
  });

  it('never touches loading while a non-empty list is present (refresh must not flash)', async () => {
    useModelStore.setState({ models: [model], loading: false, error: null });
    let release!: (v: ModelInfo[]) => void;
    vi.mocked(getModels).mockImplementation(
      () => new Promise<ModelInfo[]>((r) => { release = r; }),
    );
    const pending = useModelStore.getState().fetchModels();
    // In-flight refresh over a populated list: loading stayed false the
    // whole way (the empty-list path is the only one that may set it).
    expect(useModelStore.getState().loading).toBe(false);
    release([model]);
    await pending;
    expect(useModelStore.getState().loading).toBe(false);
    expect(useModelStore.getState().models).toHaveLength(1);
  });
});
