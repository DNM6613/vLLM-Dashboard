import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { VllmInfo } from '../../api/deployment';
import { I18nProvider } from '../../i18n';
import { VllmTab } from './VllmTab';

const baseInfo: VllmInfo = {
  uv: '0.12.15',
  uv_missing: false,
  pythons: ['3.12.7'],
  venvs: ['/home/uforce/.vllm'],
  vllm_version: '0.32.0',
  venv_name: '.vllm',
  cuda_versions: ['12.9', '13.0', '13.2'],
  available_versions: ['0.33.0', '0.32.0', '0.31.0'],
  latest_version: '0.33.0',
  selected: { version: '', runtime: 'builtin', cuda: '' },
  snapshot: { version: '', previous_version: '', venv: '' },
};

const renderTab = (info: VllmInfo) =>
  render(
    <I18nProvider>
      <VllmTab
        info={info}
        cudaVersion=""
        cudaInstallSystem
        currentToolkit=""
        pypiMirror=""
        onApply={vi.fn().mockResolvedValue(true)}
        busy={false}
      />
    </I18nProvider>,
  );

describe('VllmTab version picker', () => {
  afterEach(cleanup);

  it('lists detected PyPI versions and annotates the latest radio', () => {
    renderTab(baseInfo);
    // the tab also renders the Python interpreter combobox — pin by name
    const select = screen.getByRole('combobox', { name: 'Pinned version' }) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(
      ['0.33.0', '0.32.0', '0.31.0', '__custom'],
    );
    // before pinning the picker is disabled, showing the newest detected
    expect(select.disabled).toBe(true);
    expect(select.value).toBe('0.33.0');
    expect(screen.getByText(/Latest stable \(PyPI\)/).textContent).toContain('0.33.0');
  });

  it('picking the pinned radio lands on the newest detected version', () => {
    renderTab(baseInfo);
    fireEvent.click(screen.getByRole('radio', { name: 'Pinned version' }));
    const select = screen.getByRole('combobox', { name: 'Pinned version' }) as HTMLSelectElement;
    expect(select.disabled).toBe(false);
    expect(select.value).toBe('0.33.0');
    expect(screen.queryByPlaceholderText('0.29.0')).toBeNull();
  });

  it('choosing Other… reveals the custom input', () => {
    renderTab(baseInfo);
    fireEvent.click(screen.getByRole('radio', { name: 'Pinned version' }));
    const select = screen.getByRole('combobox', { name: 'Pinned version' }) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: '__custom' } });
    const input = screen.getByPlaceholderText('0.29.0') as HTMLInputElement;
    expect(input.value).toBe('');
    fireEvent.change(input, { target: { value: '0.35.1' } });
    expect(select.value).toBe('__custom');
  });

  it('keeps the legacy free-text input when no versions are detected', () => {
    renderTab({ ...baseInfo, available_versions: [], latest_version: '' });
    // the Python interpreter combobox stays; the version picker must not
    expect(screen.queryByRole('combobox', { name: 'Pinned version' })).toBeNull();
    fireEvent.click(screen.getByRole('radio', { name: 'Pinned version' }));
    const input = screen.getByPlaceholderText('0.29.0') as HTMLInputElement;
    expect(input.value).toBe('0.29.0');
  });
});
