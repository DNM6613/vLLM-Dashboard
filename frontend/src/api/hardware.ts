import api from './client';
import type { HardwareMetrics, SoftwareInfo } from '../types';

export const getHardwareMetrics = async (): Promise<HardwareMetrics> => {
  const response = await api.get('/hardware/metrics');
  return response.data;
};

export const getSoftwareInfo = async (): Promise<SoftwareInfo> => {
  const response = await api.get('/hardware/software');
  return response.data;
};
