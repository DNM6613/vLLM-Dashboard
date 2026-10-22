import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { ConsolePanel } from './ConsolePanel';
import { ModelStatus } from '../../types';
import type { ModelInfo } from '../../types';
import { copyText } from '../../utils/clipboard';
import { showToast } from '../ui/toast';

vi.mock('../../utils/clipboard', () => ({
  copyText: vi.fn(() => Promise.resolve(true)),
}));
vi.mock('../ui/toast', () => ({
  showToast: vi.fn(),
}));
vi.mock('./ChatTab', () => ({
  ChatTab: () => null,
}));
vi.mock('./ConnectionsPanel', () => ({
  ConnectionsPanel: () => null,
}));

// jsdom has no ResizeObserver (ConsolePanel uses it to fit the terminal).
vi.stubGlobal('ResizeObserver', class {
  observe() {}
  unobserve() {}
  disconnect() {}
});

const state = vi.hoisted(() => ({
  keyCb: null as ((e: { domEvent: KeyboardEvent; key: string }) => void) | null,
  selection: '',
}));

vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    options: Record<string, unknown>;
    cols = 80;
    rows = 24;
    constructor(options: Record<string, unknown>) {
      this.options = options;
    }
    loadAddon() {}
    open() {}
    write() {}
    reset() {}
    dispose() {}
    hasSelection() {
      return state.selection.length > 0;
    }
    getSelection() {
      return state.selection;
    }
    onKey(cb: (e: { domEvent: KeyboardEvent; key: string }) => void) {
      state.keyCb = cb;
      return { dispose() {} };
    }
  },
}));
vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class { fit() {} },
}));
vi.mock('@xterm/addon-web-links', () => ({
  WebLinksAddon: class {},
}));

const models: ModelInfo[] = [
  { id: 'm1', name: 'Model One', path: '/models/m1', status: ModelStatus.RUNNING, size_bytes: 0, locked: false },
];

function renderPanel() {
  return render(
    <I18nProvider>
      <ConsolePanel models={models} onSendResize={vi.fn()} />
    </I18nProvider>,
  );
}

async function flush(): Promise<void> {
  await new Promise((r) => setTimeout(r, 0));
}

describe('ConsolePanel Ctrl+C copy', () => {
  afterEach(() => {
    state.keyCb = null;
    state.selection = '';
    vi.mocked(copyText).mockClear();
    vi.mocked(showToast).mockClear();
    cleanup();
  });

  it('copies the terminal selection on Ctrl+C (xterm 5.5 has no native Ctrl+C copy path)', async () => {
    renderPanel();
    expect(state.keyCb).not.toBeNull();
    state.selection = 'line two: selectable text 12345';
    // xterm passes its processed key (ETX) as `key`; the handler must rely
    // on the original domEvent instead.
    state.keyCb?.({ domEvent: new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }), key: '\x03' });
    await flush();
    expect(copyText).toHaveBeenCalledWith('line two: selectable text 12345');
    // Success is silent: no "Copied" toast (user ruling 2026-10-22).
    expect(showToast).not.toHaveBeenCalled();
  });

  it('does not copy on Ctrl+C without a selection', async () => {
    renderPanel();
    state.selection = '';
    state.keyCb?.({ domEvent: new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }), key: '\x03' });
    await flush();
    expect(copyText).not.toHaveBeenCalled();
    expect(showToast).not.toHaveBeenCalled();
  });

  it('ignores Ctrl+Shift+C (browser DevTools shortcut)', async () => {
    renderPanel();
    state.selection = 'text';
    state.keyCb?.({
      domEvent: new KeyboardEvent('keydown', { key: 'c', ctrlKey: true, shiftKey: true }),
      key: '\x03',
    });
    await flush();
    expect(copyText).not.toHaveBeenCalled();
    expect(showToast).not.toHaveBeenCalled();
  });

  it('toasts an error only when the copy fails', async () => {
    vi.mocked(copyText).mockResolvedValueOnce(false);
    renderPanel();
    state.selection = 'text';
    state.keyCb?.({ domEvent: new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }), key: '\x03' });
    await flush();
    expect(copyText).toHaveBeenCalledWith('text');
    expect(showToast).toHaveBeenCalledTimes(1);
    expect(vi.mocked(showToast).mock.calls[0][1]).toBe('error');
  });
});
