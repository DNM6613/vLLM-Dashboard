import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
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
