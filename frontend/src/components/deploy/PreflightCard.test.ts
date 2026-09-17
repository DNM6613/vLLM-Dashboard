import { describe, expect, it, vi } from 'vitest';
import { displayPreflightValue } from './PreflightCard';

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
