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

export const getWsUrl = (path: string): string => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${window.location.host}${path}`;
  const key = getStoredApiKey();
  if (!key) return url;
  const sep = path.includes('?') ? '&' : '?';
  return `${url}${sep}api_key=${encodeURIComponent(key)}`;
};

export default api;
