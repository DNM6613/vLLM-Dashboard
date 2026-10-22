import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { I18nProvider } from '../i18n';
import { ApiKeyGate } from './ApiKeyGate';
import { useAuthStore } from '../stores/auth';
import { getStoredApiKey } from '../utils/apiKey';

vi.mock('../utils/apiKey', () => ({
  getStoredApiKey: vi.fn(() => ''),
  setStoredApiKey: vi.fn(),
}));
vi.mock('../api/serverConfig', () => ({
  getDashboardHealth: vi.fn(),
}));

describe('ApiKeyGate stale-key reseeding (audit 2026-10-22 FE-9)', () => {
  afterEach(() => {
    useAuthStore.setState({ authRequired: false });
    vi.clearAllMocks();
    cleanup();
  });

  it('clears the dead-key prefill once the 401 interceptor has cleared storage', () => {
    // Mounted while the key was still valid — the useState initializer
    // seeds the (soon-to-die) key even though the gate renders nothing.
    vi.mocked(getStoredApiKey).mockReturnValue('dead-key');
    useAuthStore.setState({ authRequired: false });
    const { rerender } = render(
      <I18nProvider>
        <ApiKeyGate />
      </I18nProvider>,
    );
    // 401: the interceptor clears the stored key and flips authRequired —
    // the gate's initializer never re-runs, so it must re-seed on its own.
    vi.mocked(getStoredApiKey).mockReturnValue('');
    useAuthStore.setState({ authRequired: true });
    rerender(
      <I18nProvider>
        <ApiKeyGate />
      </I18nProvider>,
    );
    // type="password": query by label rather than role (password inputs
    // expose no textbox role in jsdom's a11y tree).
    const input = screen.getByLabelText('API Key') as HTMLInputElement;
    expect(input.type).toBe('password');
    expect(input.value).toBe('');
  });
});
