import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { DeployEnvModal } from './DeployEnvModal';

// vitest has no globals (project convention): RTL's auto cleanup does not
// run, so clean up explicitly.
afterEach(() => { cleanup(); });

vi.mock('../../hooks/useDeployment', () => ({
  useDeployment: () => ({
    busy: false,
    error: null,
    state: null,
    preflight: null,
    preflightLoading: false,
    hasLoaded: false,
    loadAll: vi.fn(),
    drivers: [],
    cuda: null,
    vllm: null,
    handleApplyDriver: vi.fn(),
    handleReboot: vi.fn(),
    handleCuda: vi.fn(),
    handleVllm: vi.fn(),
    handleMirrors: vi.fn(),
    handleRollback: vi.fn(),
  }),
}));
vi.mock('../deploy/PreflightCard', () => ({ PreflightCard: () => null }));
vi.mock('../deploy/RiskPanel', () => ({ RiskPanel: () => null }));
vi.mock('../deploy/MirrorPanel', () => ({ MirrorPanel: () => null }));
vi.mock('../deploy/DriverTab', () => ({ DriverTab: () => null }));
vi.mock('../deploy/CudaTab', () => ({ CudaTab: () => null }));
vi.mock('../deploy/VllmTab', () => ({ VllmTab: () => null }));

describe('DeployEnvModal backdrop', () => {
  it('renders without its own dark backdrop (server config modal supplies the only dim layer)', () => {
    render(
      <I18nProvider>
        <DeployEnvModal onClose={() => {}} />
      </I18nProvider>,
    );
    const overlay = screen.getByRole('dialog').parentElement as HTMLElement;
    expect(overlay.className).not.toContain('bg-black/60');
    expect(overlay.className).not.toContain('backdrop-blur-sm');
    expect(overlay.className).toContain('fixed');
    expect(overlay.className).toContain('inset-0');
    expect(overlay.className).toContain('z-[95]');
  });

  it('does not close when clicking outside the panel', () => {
    const onClose = vi.fn();
    render(
      <I18nProvider>
        <DeployEnvModal onClose={onClose} />
      </I18nProvider>,
    );
    const overlay = screen.getByRole('dialog').parentElement as HTMLElement;
    fireEvent.mouseDown(overlay);
    expect(onClose).not.toHaveBeenCalled();
  });
});
