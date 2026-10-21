import { create } from 'zustand';
import { getModelRuntimeStatus } from '../api/modelStatus';
import type { ModelRuntimeStatus } from '../types';

export interface ModelStatusStore {
  model: ModelRuntimeStatus | null;
  fetchModelStatus: () => Promise<void>;
}

let _msFetchReqId = 0;

// The backend stamps a fresh timestamp on every poll; compare everything
// except it so an unchanged status does not set a new object reference —
// that would re-render the whole HomePage tree on every 5s poll
// (audit 2026-10-20 P2-13).
function _sameModel(a: ModelRuntimeStatus, b: ModelRuntimeStatus): boolean {
  return JSON.stringify({ ...a, timestamp: 0 }) === JSON.stringify({ ...b, timestamp: 0 });
}

export const useModelStatusStore = create<ModelStatusStore>((set, get) => ({
  model: null,

  fetchModelStatus: async () => {
    const reqId = ++_msFetchReqId;
    try {
      const data = await getModelRuntimeStatus();
      if (reqId === _msFetchReqId) {
        const current = get().model;
        if (!current || !_sameModel(current, data)) {
          set({ model: data });
        }
      }
    } catch (error) {
      if (reqId === _msFetchReqId) {
        console.error('Failed to fetch model status:', error);
      }
    }
  },
}));
