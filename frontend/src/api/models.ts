import api from './client';
import type { ModelInfo, CliStatus, InstallCliResponse, InstallStatusResponse, BenchmarkResult, StopResult, LaunchConfigResponse } from '../types';

const TIMEOUT_SCAN = 300_000;
const TIMEOUT_SYNC = 60_000;
const TIMEOUT_STOP = 60_000;
const TIMEOUT_DELETE = 120_000;
const TIMEOUT_BENCHMARK = 120_000;

export const getModels = async (): Promise<ModelInfo[]> => {
  const response = await api.get('/models');
  return response.data;
};

export const syncModelStatus = async (): Promise<{ synced: boolean; synced_count?: number; error?: string }> => {
  const response = await api.post('/models/sync-status', null, { timeout: TIMEOUT_SYNC });
  return response.data;
};

export const stopModel = async (modelId: string): Promise<StopResult> => {
  const response = await api.post('/models/stop', null, { params: { model_id: modelId }, timeout: TIMEOUT_STOP });
  return response.data;
};

export const startModel = async (modelId: string): Promise<{ status: string; model_id: string; command: string }> => {
  const response = await api.post('/models/start', null, { params: { model_id: modelId } });
  return response.data;
};

export const scanModels = async (): Promise<{ success: boolean; models: ModelInfo[]; count: number; removed_stale?: string[] }> => {
  const response = await api.post('/models/scan', null, { timeout: TIMEOUT_SCAN });
  return response.data;
};

export const getLaunchConfig = async (modelId: string): Promise<LaunchConfigResponse> => {
  const response = await api.get(`/models/launch-config`, { params: { model_id: modelId } });
  return response.data;
};

export const saveLaunchConfig = async (modelId: string, config: { start_command: string; env_vars: string }): Promise<void> => {
  await api.post(`/models/launch-config`, { ...config, model_id: modelId });
};

export const deleteModel = async (modelId: string): Promise<void> => {
  await api.delete(`/models/${modelId}`, { timeout: TIMEOUT_DELETE });
};

export const benchmarkModel = async (modelId: string): Promise<BenchmarkResult> => {
  const response = await api.post('/models/benchmark', null, { params: { model_id: modelId }, timeout: TIMEOUT_BENCHMARK });
  return response.data;
};

export const downloadModel = async (modelRepo: string, modelSavePath: string = '', hfMirror: boolean = true): Promise<{ pid: number; log_file: string; model_save_path?: string }> => {
  const response = await api.post('/models/download', null, {
    params: { model_repo: modelRepo, model_save_path: modelSavePath, hf_mirror: hfMirror }
  });
  return response.data;
};

export const getDownloadStatus = async (logFile: string): Promise<{ status: string; log: string; message: string; progress?: number; reason?: string }> => {
  const response = await api.get('/models/download/status', { params: { log_file: logFile } });
  return response.data;
};

export const checkCliTools = async (): Promise<CliStatus> => {
  const response = await api.get('/models/cli-status');
  return response.data;
};

export const installCliTool = async (tool: 'hf'): Promise<InstallCliResponse> => {
  const response = await api.post('/models/install-cli', null, { params: { tool } });
  return response.data;
};

export const getInstallStatus = async (tool: string, logFile: string): Promise<InstallStatusResponse> => {
  const response = await api.get('/models/install-status', { params: { tool, log_file: logFile } });
  return response.data;
};
