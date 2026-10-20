import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { HardwareCards } from './HardwareCards';
import { useHardwareStore } from '../../stores/hardware';
import type { GPUInfo } from '../../types';

const gpu: GPUInfo = {
  index: 0,
  name: 'NVIDIA GeForce RTX 5060 Ti',
  memory_total_mb: 16371,
  memory_used_mb: 15000,
  temperature_c: 45,
  power_draw_w: 50,
  power_limit_w: 150,
  utilization_gpu: 10,
  utilization_memory: 5,
  pcie_gen: 4,
  pcie_width: 8,
  pcie_rx_mbs: 11,
  pcie_tx_mbs: 3,
};

function renderCards(sshConnected: boolean) {
  useHardwareStore.setState({ gpus: [gpu], cpu: null, memory: null, disk: null });
  return render(
    <I18nProvider>
      <HardwareCards sshConnected={sshConnected} />
    </I18nProvider>,
  );
}

describe('HardwareCards PCIe layout', () => {
  afterEach(() => {
    useHardwareStore.setState({ gpus: [], cpu: null, memory: null, disk: null });
    cleanup();
  });

  it('shows Gen/width on the card title row with live Rx/Tx values', () => {
    renderCards(true);
    // Gen/width moved to the card title row (right side of the "GPU 0" line)
    expect(screen.getByText('Gen4 x8')).toBeTruthy();
    // Rx/Tx rows with live MB/s values
    expect(screen.getByText('PCIe Rx')).toBeTruthy();
    expect(screen.getByText('PCIe Tx')).toBeTruthy();
    expect(screen.getByText('11 MB/s')).toBeTruthy();
    expect(screen.getByText('3 MB/s')).toBeTruthy();
  });

  it('hides the PCIe values when not live', () => {
    renderCards(false);
    expect(screen.queryByText('Gen4 x8')).toBeNull();
    expect(screen.queryByText('11 MB/s')).toBeNull();
  });
});
