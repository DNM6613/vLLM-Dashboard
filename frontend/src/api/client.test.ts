import { beforeEach, describe, expect, it, vi } from 'vitest';

const { mockGet } = vi.hoisted(() => ({ mockGet: vi.fn() }));

vi.mock('axios', () => ({
  default: {
    create: () => ({
      get: (...args: unknown[]) => mockGet(...args),
      interceptors: {
        request: { use: () => undefined },
        response: { use: () => undefined },
      },
    }),
  },
}));

import { getWsUrl } from './client';

describe('getWsUrl (B2: one-time ticket instead of key-in-URL)', () => {
  const host = window.location.host;

  beforeEach(() => {
    localStorage.clear();
    mockGet.mockReset();
  });

  it('returns a plain URL without a ticket fetch when no key is stored', async () => {
    const url = await getWsUrl('/ws/hardware');
    expect(url).toBe(`ws://${host}/ws/hardware`);
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('appends a minted one-time ticket when a key is stored', async () => {
    localStorage.setItem('vllm_api_key', 'secret-key');
    mockGet.mockResolvedValueOnce({ data: { ticket: 'tkt-1', ttl: 60 } });
    const url = await getWsUrl('/ws/hardware');
    expect(url).toBe(`ws://${host}/ws/hardware?ticket=tkt-1`);
    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(mockGet.mock.calls[0][0]).toBe('/auth/ws-ticket');
  });

  it('uses & for paths that already carry a query string', async () => {
    localStorage.setItem('vllm_api_key', 'secret-key');
    mockGet.mockResolvedValueOnce({ data: { ticket: 'tkt-2', ttl: 60 } });
    const url = await getWsUrl('/ws/console?mode=start');
    expect(url).toBe(`ws://${host}/ws/console?mode=start&ticket=tkt-2`);
  });

  it('never puts the API key in the URL (B2 regression lock)', async () => {
    localStorage.setItem('vllm_api_key', 'secret-key');
    mockGet.mockResolvedValueOnce({ data: { ticket: 'tkt-3', ttl: 60 } });
    const url = await getWsUrl('/ws/hardware');
    expect(url).not.toContain('secret-key');
    expect(url).not.toContain('api_key=');
  });

  it('rejects when ticket minting fails', async () => {
    localStorage.setItem('vllm_api_key', 'secret-key');
    mockGet.mockRejectedValueOnce(new Error('Request failed with status code 401'));
    await expect(getWsUrl('/ws/hardware')).rejects.toThrow();
  });
});
