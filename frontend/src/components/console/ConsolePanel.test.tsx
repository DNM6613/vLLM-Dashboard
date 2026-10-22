import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { ConsolePanel } from './ConsolePanel';
import { ModelStatus } from '../../types';
import type { ModelInfo } from '../../types';
import { copyText } from '../../utils/clipboard';

vi.mock('../../utils/clipboard', () => ({
  copyText: vi.fn(() => Promise.resolve(true)),
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
  webLinks: null as ((e: unknown, uri: string) => void) | null,
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
  WebLinksAddon: class {
    constructor(handler?: (e: unknown, uri: string) => void) {
      state.webLinks = handler ?? null;
    }
  },
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
  });

  it('does not copy on Ctrl+C without a selection', async () => {
    renderPanel();
    state.selection = '';
    state.keyCb?.({ domEvent: new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }), key: '\x03' });
    await flush();
    expect(copyText).not.toHaveBeenCalled();
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
  });
});

describe('ConsolePanel web links (audit 2026-10-22 FE-12)', () => {
  afterEach(() => {
    state.webLinks = null;
    cleanup();
  });

  it('only opens well-formed http(s) links from terminal output', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    renderPanel();
    expect(state.webLinks).not.toBeNull();
    const handler = state.webLinks!;
    handler({}, 'https://docs.vllm.ai/en/latest/');
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith('https://docs.vllm.ai/en/latest/', '_blank', 'noopener,noreferrer');
    // anything else in PTY output must NOT open
    handler({}, 'file:///etc/passwd');
    handler({}, 'javascript:alert(1)');
    handler({}, 'www.example.com');
    expect(open).toHaveBeenCalledTimes(1);
    open.mockRestore();
  });
});
