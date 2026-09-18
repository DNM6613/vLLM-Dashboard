import { create } from 'zustand';
import { getModelRuntimeStatus } from '../api/modelStatus';
import type { ModelRuntimeStatus } from '../types';

export interface ModelStatusStore {
  model: ModelRuntimeStatus | null;
  fetchModelStatus: () => Promise<void>;
}

let _msFetchReqId = 0;

export const useModelStatusStore = create<ModelStatusStore>((set) => ({
  model: null,

  fetchModelStatus: async () => {
    const reqId = ++_msFetchReqId;
    try {
      const data = await getModelRuntimeStatus();
      if (reqId === _msFetchReqId) {
        set({ model: data });
      }
    } catch (error) {
      if (reqId === _msFetchReqId) {
        console.error('Failed to fetch model status:', error);
      }
    }
  },
}));
