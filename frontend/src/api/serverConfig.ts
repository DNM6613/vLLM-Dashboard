import api from './client';
import type { ServerConfig, ServerHealth, SshStatus, BmcStatus, PowerAction } from '../types';

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

export interface PowerActionResult {
  status: 'success';
  action: PowerAction;
}

// One BMC chassis power command (off/soft/reset/cycle): a single ipmitool
// round trip on the backend, so the default 30s budget is plenty (unlike
// power-on, which verifies for up to ~120s).
export const powerOffServer = async (action: PowerAction): Promise<PowerActionResult> => {
  const response = await api.post<PowerActionResult>('/config/server/shutdown', { action });
  return response.data;
};

export interface PowerOnResult {
  status: 'success';
  powered_on: boolean;
}

// The backend verifies the power-on through BMC status AND a network/SSH
// probe of the target, retrying until one confirms (up to ~120s), so this
// needs a longer budget than the default 30s.
export const powerOnServer = async (): Promise<PowerOnResult> => {
  const response = await api.post<PowerOnResult>('/config/server/poweron', null, { timeout: 150000 });
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
