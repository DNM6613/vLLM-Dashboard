import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useModelStatusStore } from './modelStatus';
import { getModelRuntimeStatus } from '../api/modelStatus';
import type { ModelRuntimeStatus } from '../types';

vi.mock('../api/modelStatus', () => ({
  getModelRuntimeStatus: vi.fn(),
}));

const base: ModelRuntimeStatus = {
  status: 'connected',
  models: ['model-a'],
  running_requests: 0,
  waiting_requests: null,
  kv_cache_usage_pct: null,
  generation_tokens_per_s: null,
  prompt_tokens_per_s: null,
  generation_tokens_total: null,
  prompt_tokens_total: null,
  mtp_hit_rate_pct: null,
  prefix_cache_hit_rate_pct: null,
  mtp_hit_rate_cumulative_pct: null,
  prefix_cache_hit_rate_cumulative_pct: null,
  avg_ttft_s: null,
  avg_tpot_ms: null,
  avg_e2e_latency_s: null,
  admission_rejections_total: null,
  num_preemptions_total: null,
  avg_queue_s: null,
  avg_prefill_s: null,
  avg_decode_s: null,
  api_port: 8000,
  api_key: null,
  timestamp: 0,
};

// audit 2026-10-20 P2-13: the backend stamps a fresh timestamp on every 5s
// poll; the store must not set a new object reference when nothing else
// changed, or the whole HomePage tree re-renders on every poll.
describe('modelStatus store', () => {
  beforeEach(() => {
    vi.mocked(getModelRuntimeStatus).mockReset();
    useModelStatusStore.setState({ model: null });
  });

  it('keeps the same reference when the polled status is unchanged', async () => {
    vi.mocked(getModelRuntimeStatus).mockResolvedValue({ ...base, timestamp: 1 });
    await useModelStatusStore.getState().fetchModelStatus();
    const first = useModelStatusStore.getState().model;
    expect(first).toBeTruthy();

    vi.mocked(getModelRuntimeStatus).mockResolvedValue({ ...base, timestamp: 2 });
    await useModelStatusStore.getState().fetchModelStatus();
    expect(useModelStatusStore.getState().model).toBe(first);
  });

  it('sets a new reference when a field changes', async () => {
    vi.mocked(getModelRuntimeStatus).mockResolvedValue({ ...base, timestamp: 1 });
    await useModelStatusStore.getState().fetchModelStatus();
    const first = useModelStatusStore.getState().model;

    vi.mocked(getModelRuntimeStatus).mockResolvedValue({ ...base, running_requests: 3, timestamp: 2 });
    await useModelStatusStore.getState().fetchModelStatus();
    expect(useModelStatusStore.getState().model).not.toBe(first);
  });
});
