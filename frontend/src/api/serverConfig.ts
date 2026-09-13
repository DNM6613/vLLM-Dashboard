import api from './client';
import type { ServerConfig, ServerHealth, SshStatus, BmcStatus } from '../types';

export const getServerConfig = async (): Promise<ServerConfig> => {
  const response = await api.get('/config/server');
  return response.data;
};

export const updateServerConfig = async (config: Partial<ServerConfig>): Promise<ServerConfig> => {
  const response = await api.post('/config/server', config);
  return response.data.config;
};

export const getServerHealth = async (): Promise<ServerHealth> => {
  const response = await api.get('/config/server/health');
  return response.data;
};

export const getDashboardHealth = async (): Promise<ServerHealth> => {
  const response = await api.get<ServerHealth>('/health', { baseURL: '' });
  return response.data;
};

export const getSshStatus = async (): Promise<SshStatus> => {
  const response = await api.get('/config/ssh/status');
  return response.data;
};

export const getBmcStatus = async (): Promise<BmcStatus> => {
  const response = await api.get<BmcStatus>('/config/server/bmc-status');
  return response.data;
};

export interface ShutdownResult {
  status: 'success' | 'timeout';
  stopped: boolean;
}

export const shutdownServer = async (): Promise<ShutdownResult> => {
  const response = await api.post<ShutdownResult>('/config/server/shutdown');
  return response.data;
};

export interface PowerOnResult {
  status: 'success';
  powered_on: boolean;
}

export const powerOnServer = async (): Promise<PowerOnResult> => {
  const response = await api.post<PowerOnResult>('/config/server/poweron');
  return response.data;
};

export interface BmcResetResult {
  status: 'success';
  reset: number;
}

export const resetModelsAfterPowerOff = async (): Promise<{ reset: number }> => {
  const response = await api.post<BmcResetResult>('/config/server/bmc-reset');
  return response.data;
};
