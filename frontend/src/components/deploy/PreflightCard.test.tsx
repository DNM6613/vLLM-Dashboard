import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { Preflight } from '../../api/deployment';
import { I18nProvider } from '../../i18n';
import { displayPreflightValue, PreflightCard } from './PreflightCard';

describe('displayPreflightValue', () => {
  it('disk: translates the frame, keeps the live number', () => {
    const t = vi.fn((key: string) => key);
    expect(displayPreflightValue({ key: 'disk', value: '778.1 GB free' }, t))
      .toBe('{n} GB free');
    expect(t).toHaveBeenCalledWith('{n} GB free', { n: '778.1' });
  });

  it('network: translates the prefix, keeps the host list', () => {
    const t = vi.fn((key: string) => key);
    expect(
      displayPreflightValue(
        { key: 'network', value: 'reachable: pypi.org, mirrors.aliyun.com' },
        t,
      ),
    ).toBe('reachable: {hosts}');
    expect(t).toHaveBeenCalledWith('reachable: {hosts}', {
      hosts: 'pypi.org, mirrors.aliyun.com',
    });
  });

  it('static value: direct lookup', () => {
    const t = vi.fn((key: string) => key);
    expect(displayPreflightValue({ key: 'driver_tool', value: 'available' }, t))
      .toBe('available');
    expect(t).toHaveBeenCalledWith('available');
  });

  it('kernel/OS/uv values are data: passthrough unchanged', () => {
    const t = vi.fn((key: string) => key);
    expect(displayPreflightValue({ key: 'kernel', value: '7.0.0-31-generic' }, t))
      .toBe('7.0.0-31-generic');
    expect(t).toHaveBeenCalledWith('7.0.0-31-generic');
  });
});

const preflight: Preflight = {
  ok: true,
  blocking: false,
  items: [{ key: 'driver_tool', label: 'Driver tool', status: 'ok', value: 'available', detail: '' }],
  checked_at: '2026-09-18T00:00:00Z',
};

describe('PreflightCard', () => {
  afterEach(cleanup);

  it('hides Re-check on the first render before the initial load settles', () => {
    render(
      <I18nProvider>
        <PreflightCard preflight={null} loading={false} hasLoaded={false} onRefresh={vi.fn()} />
      </I18nProvider>,
    );
    expect(screen.queryByText('Re-check')).toBeNull();
  });

  it('hides Re-check while a load is in progress', () => {
    render(
      <I18nProvider>
        <PreflightCard preflight={preflight} loading hasLoaded onRefresh={vi.fn()} />
      </I18nProvider>,
    );
    expect(screen.queryByText('Re-check')).toBeNull();
  });

  it('shows Re-check once the initial load settles', () => {
    render(
      <I18nProvider>
        <PreflightCard preflight={preflight} loading={false} hasLoaded onRefresh={vi.fn()} />
      </I18nProvider>,
    );
    expect(screen.getByText('Re-check')).toBeTruthy();
  });

  it('Re-check click triggers onRefresh', () => {
    const onRefresh = vi.fn();
    render(
      <I18nProvider>
        <PreflightCard preflight={preflight} loading={false} hasLoaded onRefresh={onRefresh} />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByText('Re-check'));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });
});
