import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import type { CudaInfo } from '../../api/deployment';
import { I18nProvider } from '../../i18n';
import { CudaTab } from './CudaTab';

const baseInfo: CudaInfo = {
  versions: ['13.2', '13.4'],
  versions_source: 'repo',
  min_driver: { '12.9': 575, '13.0': 580, '13.1': 590, '13.2': 595, '13.3': 610, '13.4': 615 },
  selected: '',
  install_system: true,
  driver_major: 595,
  selected_driver_major: null,
  driver_max_cuda: '13.2',
  selected_driver_max_cuda: '',
  current_toolkit: '13.0',
  installed_toolkit: '',
};

const renderTab = (info: CudaInfo) =>
  render(
    <I18nProvider>
      <CudaTab
        locked={false}
        info={info}
        pickedVersion=""
        onPickVersion={vi.fn()}
        installSystem
        onInstallSystemChange={vi.fn()}
        onApply={vi.fn().mockResolvedValue(true)}
        busy={false}
      />
    </I18nProvider>,
  );

describe('CudaTab', () => {
  afterEach(cleanup);

  it('renders the versions from the live repo probe', () => {
    renderTab(baseInfo);
    expect(screen.getByText('CUDA 13.2')).toBeTruthy();
    expect(screen.getByText('CUDA 13.4')).toBeTruthy();
    expect(
      screen.queryByText('NVIDIA repo query failed — showing the built-in version list'),
    ).toBeNull();
  });

  it('shows a fallback notice when the repo probe degraded', () => {
    renderTab({ ...baseInfo, versions_source: 'fallback' });
    expect(
      screen.getByText('NVIDIA repo query failed — showing the built-in version list'),
    ).toBeTruthy();
  });
});
