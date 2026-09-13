import api from './client';
import type { ModelRuntimeStatus } from '../types';

export const getModelRuntimeStatus = async (): Promise<ModelRuntimeStatus> => {
  const response = await api.get('/models/runtime-status');
  return response.data;
};
