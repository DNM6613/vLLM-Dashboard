import api from './client';

export interface ConnectionInfo {
  ip: string;
  count: number;
  last_seen: number;
  last_method: string;
  last_path: string;
  note: string;
}

export interface RequestEntry {
  ts: number;
  ip: string;
  method: string;
  path: string;
  status: number;
  body: string | null;
}

export const getConnections = async (): Promise<ConnectionInfo[]> => {
  const response = await api.get<{ connections: ConnectionInfo[] }>('/monitor/connections');
  return response.data.connections;
};

export const getRequests = async (ip: string, limit = 100): Promise<RequestEntry[]> => {
  const response = await api.get<{ requests: RequestEntry[] }>(
    `/monitor/requests?ip=${encodeURIComponent(ip)}&limit=${limit}`,
  );
  return response.data.requests;
};

export const setConnectionNote = async (ip: string, note: string): Promise<void> => {
  await api.put(`/monitor/notes/${encodeURIComponent(ip)}`, { note });
};
