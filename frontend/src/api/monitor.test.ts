import { afterEach, describe, expect, it, vi } from 'vitest';
import api from './client';
import { getConnections, getRequests } from './monitor';

describe('monitor api', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('getConnections returns the connections array', async () => {
    const rows = [{ ip: '1.2.3.4', count: 3, last_seen: 1700000000, last_method: 'GET', last_path: '/api/v1/models' }];
    vi.spyOn(api, 'get').mockResolvedValue({ data: { connections: rows } } as never);
    await expect(getConnections()).resolves.toEqual(rows);
    expect(api.get).toHaveBeenCalledWith('/monitor/connections');
  });

  it('getRequests encodes the IP and limit into the query string', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ data: { requests: [] } } as never);
    await getRequests('1.2.3.4', 50);
    expect(api.get).toHaveBeenCalledWith('/monitor/requests?ip=1.2.3.4&limit=50');
  });

  it('getRequests defaults to limit 100', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ data: { requests: [] } } as never);
    await getRequests('10.0.0.1');
    expect(api.get).toHaveBeenCalledWith('/monitor/requests?ip=10.0.0.1&limit=100');
  });
});
