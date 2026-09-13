import { create } from 'zustand';
import * as modelsApi from '../api/models';
import { mapBackendError, tRaw } from '../i18n/strings';
import type { ModelStore } from '../types';

export function errMsg(e: unknown): string {
  let msg = '';
  if (e instanceof Error) {
    msg = e.message;
  } else if (typeof e === 'string') {
    msg = e;
  } else if (e && typeof e === 'object') {
    const err = e as Record<string, unknown>;
    const resp = err.response as Record<string, unknown> | undefined;
    if (resp) {
      const data = resp.data as Record<string, unknown> | undefined;
      const errorObj = (data?.error ?? undefined) as Record<string, unknown> | undefined;
      if (errorObj && typeof errorObj.message === 'string') msg = errorObj.message;
      else if (typeof data?.detail === 'string') msg = data.detail;
      else if (typeof data?.message === 'string') msg = data.message;
    }
  }
  if (msg === 'Network Error') return tRaw('Network error');
  if (/^timeout of \d+ms exceeded$/.test(msg)) return tRaw('Request timeout');
  return msg;
}

export function errMsgLocalized(e: unknown): string {
  return mapBackendError(errMsg(e));
}

let _fetchModelsReqId = 0;

export function invalidateModelFetch(): void {
  _fetchModelsReqId++;
}

export const useModelStore = create<ModelStore>((set) => ({
  models: [],
  loading: false,
  error: null,

  fetchModels: async () => {
    const reqId = ++_fetchModelsReqId;
    set({ loading: true, error: null });
    try {
      const models = await modelsApi.getModels();
      if (reqId === _fetchModelsReqId) {
        set({ models, loading: false });
      }
    } catch (error: unknown) {
      if (reqId === _fetchModelsReqId) {
        set({ error: errMsgLocalized(error) || 'Failed to fetch models', loading: false });
      }
    }
  },

  syncModelStatus: async () => {
    try {
      const result = await modelsApi.syncModelStatus();
      return result;
    } catch (error: unknown) {
      console.error('Failed to sync model status:', error);
      return { synced: false };
    }
  },

  stopModel: async (modelId) => {
    return await modelsApi.stopModel(modelId);
  },

  startModel: async (modelId) => {
    return await modelsApi.startModel(modelId);
  },

  clearError: () => set({ error: null }),

  scanModels: async () => {
    try {
      const result = await modelsApi.scanModels();
      return result;
    } catch (error: unknown) {
      const msg = errMsgLocalized(error);
      console.error('Failed to scan models:', msg);
      return { count: 0, error: msg || 'Scan failed' };
    }
  },

  getLaunchConfig: async (modelId) => {
    return await modelsApi.getLaunchConfig(modelId);
  },

  saveLaunchConfig: async (modelId, config) => {
    await modelsApi.saveLaunchConfig(modelId, config);
  },

  deleteModel: async (modelId) => {
    try {
      await modelsApi.deleteModel(modelId);
      set((state) => ({
        models: state.models.filter(m => m.id !== modelId)
      }));
    } catch (error: unknown) {
      console.error('Failed to delete model:', errMsg(error));
      throw error;
    }
  },
}));
