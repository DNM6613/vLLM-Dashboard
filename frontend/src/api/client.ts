import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from 'axios';
import { getStoredApiKey } from '../utils/apiKey';
import { useAuthStore } from '../stores/auth';

const API_BASE_URL = '/api/v1';

const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const key = getStoredApiKey();
  if (key) {
    config.headers.set('X-API-Key', key);
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    if (status === 401) {
      useAuthStore.getState().requestAuth();
    }
    return Promise.reject(error);
  },
);

// B2 (audit 2026-10-16): the API key no longer travels in the WS URL.
// When a key is stored we mint a one-time short-lived ticket over HTTP
// (key sent in the X-API-Key header) and connect with `?ticket=...` instead.
export const fetchWsTicket = async (): Promise<string> => {
  const res = await api.get<{ ticket: string; ttl: number }>('/auth/ws-ticket');
  return res.data.ticket;
};

export const getWsUrl = async (path: string): Promise<string> => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${window.location.host}${path}`;
  const key = getStoredApiKey();
  if (!key) return url;
  const ticket = await fetchWsTicket();
  const sep = path.includes('?') ? '&' : '?';
  return `${url}${sep}ticket=${encodeURIComponent(ticket)}`;
};

export default api;
