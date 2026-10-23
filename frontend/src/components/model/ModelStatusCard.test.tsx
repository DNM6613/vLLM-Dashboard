import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { ModelStatusCard } from './ModelStatusCard';
import { useModelStatusStore } from '../../stores/modelStatus';
import type { ModelRuntimeStatus } from '../../types';

const model: ModelRuntimeStatus = {
  status: 'connected',
  models: ['model-a'],
  running_requests: 2,
  waiting_requests: 1,
  kv_cache_usage_pct: 40,
  generation_tokens_per_s: 10,
  prompt_tokens_per_s: 5,
  generation_tokens_total: 100,
  prompt_tokens_total: 50,
  mtp_hit_rate_pct: 70,
  prefix_cache_hit_rate_pct: 60,
  mtp_hit_rate_cumulative_pct: 65,
  prefix_cache_hit_rate_cumulative_pct: 55,
  avg_ttft_s: 0.5,
  avg_tpot_ms: 20,
  avg_e2e_latency_s: 9,
  admission_rejections_total: 3,
  num_preemptions_total: 4,
  avg_queue_s: 2.5,
  avg_prefill_s: 4,
  avg_decode_s: 22,
  api_port: 8000,
  api_key: null,
  timestamp: 0,
};

function renderCard() {
  return render(
    <I18nProvider>
      <ModelStatusCard />
    </I18nProvider>,
  );
}

describe('ModelStatusCard detailed metrics collapse', () => {
  beforeEach(() => {
    useModelStatusStore.setState({ model });
  });

  afterEach(() => {
    cleanup();
  });

  it('keeps the detailed metrics collapsed by default and expands on click', () => {
    renderCard();
    const toggle = screen.getByLabelText('Detailed Metrics');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });

  it('shows rejections and preemptions as one combined row', () => {
    renderCard();
    fireEvent.click(screen.getByLabelText('Detailed Metrics'));
    expect(screen.getByText('Rejections/Preemptions (Total)')).toBeTruthy();
    expect(screen.queryByText('Admission Rejections (Total)')).toBeNull();
    expect(screen.queryByText('Preemptions (Total)')).toBeNull();
  });

  it('renders TTFT and E2E latency as separate rows', () => {
    renderCard();
    fireEvent.click(screen.getByLabelText('Detailed Metrics'));
    expect(screen.getByText('Avg TTFT (s)')).toBeTruthy();
    expect(screen.getByText('Avg E2E Latency (s)')).toBeTruthy();
    expect(screen.queryByText('Avg TTFT/E2E (s)')).toBeNull();
  });
});
