import { afterEach, describe, expect, it, vi } from 'vitest';
import { copyText } from './clipboard';

function setSecureContext(value: boolean) {
  Object.defineProperty(window, 'isSecureContext', { value, configurable: true });
}

function setClipboard(clip: unknown) {
  Object.defineProperty(navigator, 'clipboard', { value: clip, configurable: true });
}

// jsdom does not implement the legacy document.execCommand — provide it per test.
function setExecCommand(impl: () => boolean) {
  document.execCommand = vi.fn(impl);
}

describe('copyText', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('uses the async clipboard API in a secure context', async () => {
    setSecureContext(true);
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText });
    const execSpy = vi.fn();
    document.execCommand = execSpy;
    await expect(copyText('model-name')).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith('model-name');
    expect(execSpy).not.toHaveBeenCalled();
  });

  it('falls back to execCommand when the clipboard API is unavailable (plain http)', async () => {
    setSecureContext(false);
    setClipboard(undefined);
    setExecCommand(() => true);
    await expect(copyText('model-name')).resolves.toBe(true);
    expect(document.execCommand).toHaveBeenCalledWith('copy');
  });

  it('falls back to execCommand when the clipboard API rejects', async () => {
    setSecureContext(true);
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('denied')) });
    setExecCommand(() => true);
    await expect(copyText('model-name')).resolves.toBe(true);
    expect(document.execCommand).toHaveBeenCalledWith('copy');
  });

  it('reports failure when execCommand fails', async () => {
    setSecureContext(false);
    setClipboard(undefined);
    setExecCommand(() => false);
    await expect(copyText('model-name')).resolves.toBe(false);
  });
});
