import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n';
import { downloadModel } from '../api/models';
import { showToast } from '../components/ui/toast';
import { useModelDownload } from './useModelDownload';

vi.mock('../api/models', () => ({
  checkCliTools: vi.fn().mockResolvedValue({ hf_installed: true }),
  downloadModel: vi.fn().mockResolvedValue({ log_file: '/tmp/dl.log', model_save_path: '' }),
  getDownloadStatus: vi.fn().mockResolvedValue({ status: 'downloading', progress: 50 }),
  getInstallStatus: vi.fn().mockResolvedValue({ status: 'complete' }),
  installCliTool: vi.fn().mockResolvedValue({ log_file: '/tmp/install.log' }),
  stopDownload: vi.fn().mockResolvedValue({ success: true }),
}));

vi.mock('../stores/model', () => ({
  useModelStore: vi.fn(() => ({ fetchModels: vi.fn(), scanModels: vi.fn() })),
  errMsgLocalized: vi.fn((e: unknown) => (e instanceof Error ? e.message : String(e))),
}));

vi.mock('../components/ui/toast', () => ({
  showToast: vi.fn(),
}));

function renderDownload(hfToken: string, onSaveHfToken: (v: string) => Promise<void>) {
  return renderHook(
    () => useModelDownload({
      defaultSavePath: '', onSavePathPersisted: vi.fn(),
      hfToken, onSaveHfToken,
    }),
    { wrapper: I18nProvider },
  );
}

// The token is set AFTER the modal opens: opening resets the field to the
// server-side value (mock '***' or ''), so earlier edits would be clobbered.
async function openAndStart(result: ReturnType<typeof renderDownload>['result'], token?: string) {
  await act(async () => { result.current.setShowDownloadModal(true); });
  await waitFor(() => expect(result.current.checkingCli).toBe(false));
  act(() => { result.current.setDownloadModelName('org/repo'); });
  if (token !== undefined) act(() => { result.current.setHfToken(token); });
  await act(async () => { await result.current.handleDownloadModel(); });
}

describe('useModelDownload HF token save', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('saves a changed token before starting the download', async () => {
    const onSaveHfToken = vi.fn().mockResolvedValue(undefined);
    const { result } = renderDownload('', onSaveHfToken);
    await openAndStart(result, 'hf_abc123');
    expect(onSaveHfToken).toHaveBeenCalledTimes(1);
    expect(onSaveHfToken).toHaveBeenCalledWith('hf_abc123');
    expect(vi.mocked(downloadModel)).toHaveBeenCalledTimes(1);
    expect(onSaveHfToken.mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(downloadModel).mock.invocationCallOrder[0]);
  });

  it('does not send a token save when the field is left untouched (incl. the *** mask)', async () => {
    const onSaveHfToken = vi.fn();
    const { result } = renderDownload('***', onSaveHfToken);
    await openAndStart(result);
    expect(onSaveHfToken).not.toHaveBeenCalled();
    expect(vi.mocked(downloadModel)).toHaveBeenCalledTimes(1);
  });

  it('does not start the download when the token save fails', async () => {
    const onSaveHfToken = vi.fn().mockRejectedValue(new Error('boom'));
    const { result } = renderDownload('', onSaveHfToken);
    await openAndStart(result, 'hf_abc123');
    expect(vi.mocked(downloadModel)).not.toHaveBeenCalled();
    expect(vi.mocked(showToast)).toHaveBeenCalled();
  });
});
