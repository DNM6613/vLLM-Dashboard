import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { ChatTab } from './ChatTab';
import { ModelStatus } from '../../types';
import type { ModelInfo } from '../../types';
import type { ChatEntry } from '../../hooks/useChat';

vi.mock('../../hooks/useChat', () => ({
  useChat: () => mockChat,
}));

vi.mock('../../api/models', () => ({
  getLaunchConfig: vi.fn(() =>
    Promise.resolve({ config: { start_command: 'vllm serve --reasoning-parser qwen3' } }),
  ),
}));

const mockChat = {
  entries: [] as ChatEntry[],
  streaming: false,
  send: vi.fn(),
  stop: vi.fn(),
  clear: vi.fn(),
  queue: [] as string[],
  removeQueued: vi.fn(),
  editQueued: vi.fn(),
};

const models: ModelInfo[] = [
  { id: 'm1', name: 'Model One', path: '/models/m1', status: ModelStatus.RUNNING, size_bytes: 0, locked: false },
];

function entry(reasoning: string, streaming: boolean): ChatEntry {
  return {
    id: 1,
    role: 'assistant',
    content: '',
    reasoning,
    streaming,
    error: null,
    usage: null,
    elapsedMs: null,
  };
}

function renderChat() {
  return render(
    <I18nProvider>
      <ChatTab models={models} />
    </I18nProvider>,
  );
}

// jsdom has no layout: mock the block's dimensions so the pin-to-bottom
// guard (distance from bottom < 40px) is observable.
function mockDimensions(pre: HTMLPreElement, scrollHeight: number, clientHeight: number): void {
  Object.defineProperty(pre, 'scrollHeight', { configurable: true, writable: true, value: scrollHeight });
  Object.defineProperty(pre, 'clientHeight', { configurable: true, writable: true, value: clientHeight });
}

describe('ChatTab thinking block auto-scroll', () => {
  afterEach(() => {
    mockChat.entries = [];
    mockChat.streaming = false;
    cleanup();
  });

  it('pins the streaming thinking block to its newest line as reasoning grows', () => {
    mockChat.entries = [entry('hello', true)];
    const { rerender } = renderChat();
    const pre = screen.getByText('hello') as HTMLPreElement;
    mockDimensions(pre, 120, 100);
    mockChat.entries = [entry('hello world', true)];
    rerender(
      <I18nProvider>
        <ChatTab models={models} />
      </I18nProvider>,
    );
    expect(pre.scrollTop).toBe(120);
  });

  it('does not yank back a user who scrolled up to read earlier reasoning', () => {
    mockChat.entries = [entry('a', true)];
    const { rerender } = renderChat();
    const pre = screen.getByText('a') as HTMLPreElement;
    mockDimensions(pre, 1000, 100);
    pre.scrollTop = 0;
    mockChat.entries = [entry('a b', true)];
    rerender(
      <I18nProvider>
        <ChatTab models={models} />
      </I18nProvider>,
    );
    expect(pre.scrollTop).toBe(0);
  });

  it('does not auto-scroll completed thinking blocks (ref attaches only while streaming)', () => {
    mockChat.entries = [entry('done', false)];
    const { rerender } = renderChat();
    const pre = screen.getByText('done') as HTMLPreElement;
    mockDimensions(pre, 500, 100);
    mockChat.entries = [entry('done more', false)];
    rerender(
      <I18nProvider>
        <ChatTab models={models} />
      </I18nProvider>,
    );
    expect(pre.scrollTop).toBe(0);
  });
});

function entryWithContent(content: string): ChatEntry {
  return {
    id: 1,
    role: 'assistant',
    content,
    reasoning: '',
    streaming: false,
    error: null,
    usage: null,
    elapsedMs: null,
  };
}

// The transcript container is the only DIV with overflow-y-auto in ChatTab
// (the thinking block is a PRE).
function getScrollEl(): HTMLElement {
  const els = Array.from(document.querySelectorAll<HTMLElement>('.overflow-y-auto'));
  const div = els.find((el) => el.tagName === 'DIV');
  if (!div) throw new Error('scroll container not found');
  return div;
}

describe('ChatTab transcript auto-scroll (FE-3 follow-up: intent-based pinning)', () => {
  afterEach(() => {
    mockChat.entries = [];
    cleanup();
  });

  // jsdom fires no 'scroll' event for programmatic scrollTop changes, so the
  // tests dispatch one explicitly — in a real browser the user's scroll
  // (or our own pinning) is what updates the follow-intent.
  it('pins the transcript to the newest line while the user is near the bottom', () => {
    mockChat.entries = [entryWithContent('first')];
    const { rerender } = renderChat();
    const el = getScrollEl();
    Object.defineProperty(el, 'scrollHeight', { configurable: true, writable: true, value: 120 });
    Object.defineProperty(el, 'clientHeight', { configurable: true, writable: true, value: 100 });
    el.scrollTop = 110; // distance from bottom < 40
    fireEvent.scroll(el);
    mockChat.entries = [entryWithContent('first'), entryWithContent('second')];
    rerender(
      <I18nProvider>
        <ChatTab models={models} />
      </I18nProvider>,
    );
    expect(el.scrollTop).toBe(120);
  });

  it('does not yank back a user who scrolled up through the transcript', () => {
    mockChat.entries = [entryWithContent('first')];
    const { rerender } = renderChat();
    const el = getScrollEl();
    Object.defineProperty(el, 'scrollHeight', { configurable: true, writable: true, value: 1000 });
    Object.defineProperty(el, 'clientHeight', { configurable: true, writable: true, value: 100 });
    el.scrollTop = 0; // distance from bottom > 40
    fireEvent.scroll(el); // user scrolled up → intent = stay put
    mockChat.entries = [entryWithContent('first'), entryWithContent('second')];
    rerender(
      <I18nProvider>
        <ChatTab models={models} />
      </I18nProvider>,
    );
    expect(el.scrollTop).toBe(0);
  });

  it('keeps pinning when a single update grows the transcript by more than 40px while the user is at the bottom', () => {
    // Regression for the FE-3 guard: it read the ALREADY-GROWN distance, so
    // one update adding > 40px (a multi-line send appends user + model
    // entries at once) stopped auto-follow forever until a manual scroll.
    // Intent now comes from the last scroll event, which content growth
    // does not fire.
    mockChat.entries = [entryWithContent('first')];
    const { rerender } = renderChat();
    const el = getScrollEl();
    Object.defineProperty(el, 'scrollHeight', { configurable: true, writable: true, value: 100 });
    Object.defineProperty(el, 'clientHeight', { configurable: true, writable: true, value: 100 });
    el.scrollTop = 0; // sitting exactly at the bottom
    fireEvent.scroll(el);
    // one update grows content by 300px (no 'scroll' event for that)
    Object.defineProperty(el, 'scrollHeight', { configurable: true, writable: true, value: 400 });
    mockChat.entries = [entryWithContent('first'), entryWithContent('second, much longer')];
    rerender(
      <I18nProvider>
        <ChatTab models={models} />
      </I18nProvider>,
    );
    expect(el.scrollTop).toBe(400);
  });
});

describe('ChatTab thinking icon toggle (brain icon beside clear)', () => {
  afterEach(() => {
    mockChat.entries = [];
    localStorage.removeItem('vllm_chat_show_thinking');
    cleanup();
  });

  it('shows a brain icon beside the clear icon that toggles the thinking blocks', async () => {
    mockChat.entries = [entry('deep reasoning text', true)];
    renderChat();
    // thinkingSupported arrives async (mocked getLaunchConfig carries --reasoning-parser)
    const brain = await screen.findByTitle('Show thinking');
    expect(screen.getByTitle('Clear chat')).toBeTruthy(); // side by side with clear
    expect(brain.getAttribute('aria-pressed')).toBe('true'); // default on
    expect(screen.getByText('deep reasoning text')).toBeTruthy();
    fireEvent.click(brain);
    expect(brain.getAttribute('aria-pressed')).toBe('false');
    expect(screen.queryByText('deep reasoning text')).toBeNull();
    fireEvent.click(brain);
    expect(brain.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText('deep reasoning text')).toBeTruthy();
  });
});

describe('ChatTab clear button (audit 2026-10-22 FE-10)', () => {
  afterEach(() => {
    mockChat.entries = [];
    vi.mocked(mockChat.clear).mockClear();
    cleanup();
  });

  it('always offers a clear button that calls clear() (visible even on an empty transcript)', () => {
    mockChat.entries = [entryWithContent('hello')];
    const { unmount } = renderChat();
    const btn = screen.getByTitle('Clear chat');
    fireEvent.click(btn);
    expect(mockChat.clear).toHaveBeenCalledTimes(1);

    // empty transcript: the button stays visible (2026-10-22 user ruling:
    // 对话框为空时清空按钮不必隐藏)
    unmount();
    mockChat.entries = [];
    renderChat();
    expect(screen.getByTitle('Clear chat')).toBeTruthy();
  });
});
