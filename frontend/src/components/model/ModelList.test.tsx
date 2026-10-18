import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { ModelList } from './ModelList';
import { ModelStatus } from '../../types';
import type { ModelInfo } from '../../types';

const baseProps = {
  loading: false,
  refreshing: false,
  scanMessage: null,
  error: null,
  starting: false,
  deletingId: null,
  stoppingId: null,
  lockingId: null,
  sshConnected: true,
  benchmarkResults: {},
  onRefresh: () => { },
  onStart: () => { },
  onStop: () => { },
  onToggleLock: () => { },
  onOpenLaunchConfig: () => { },
  onDelete: () => { },
  onOpenDownload: () => { },
  onOpenBenchmark: () => { },
  download: null,
  onCancelDownload: () => { },
  openaiEndpoint: null,
  openaiApiKey: null,
};

const mk = (status: ModelInfo['status']): ModelInfo => ({
  id: 'm1', name: 'Model One', path: '/models/m1', status, size_bytes: 0, locked: false,
});

function renderRow(status: ModelInfo['status']) {
  return render(
    <I18nProvider>
      <ModelList models={[mk(status)]} {...baseProps} />
    </I18nProvider>,
  );
}

describe('ModelList lock button', () => {
  afterEach(cleanup);

  it('shows the lock button only for running models', () => {
    renderRow(ModelStatus.RUNNING);
    expect(screen.queryByRole('button', { name: /auto-restart on crash/ })).toBeTruthy();
    cleanup();
    renderRow(ModelStatus.STOPPED);
    expect(screen.queryByRole('button', { name: /auto-restart on crash/ })).toBeNull();
  });

  it('places the lock button left of the start/stop buttons', () => {
    const { container } = renderRow(ModelStatus.RUNNING);
    const labels = Array.from(container.querySelectorAll('button')).map((b) => b.getAttribute('aria-label') ?? '');
    const lockIdx = labels.findIndex((l) => l.startsWith('Lock'));
    const stopIdx = labels.indexOf('Stop');
    expect(lockIdx).toBeGreaterThanOrEqual(0);
    expect(stopIdx).toBeGreaterThanOrEqual(0);
    expect(lockIdx).toBeLessThan(stopIdx);
  });
});
