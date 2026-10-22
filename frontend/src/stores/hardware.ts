import { create } from 'zustand';
import { getHardwareMetrics, getSoftwareInfo } from '../api/hardware';
import type { HardwareStore } from '../types';

let _hwFetchReqId = 0;

export function invalidateHardwareFetch(): void {
  _hwFetchReqId++;
}

let _swFetchReqId = 0;

export function invalidateSoftwareFetch(): void {
  _swFetchReqId++;
}

export const useHardwareStore = create<HardwareStore>((set) => ({
  gpus: [],
  cpu: null,
  memory: null,
  disk: null,
  software: null,
  lastUpdated: null,

  addMetrics: (payload) => {
    const gpus = payload.gpus || [];
    set((state) => ({
      gpus,
      cpu: payload.cpu || state.cpu,
      memory: payload.memory || state.memory,
      disk: payload.disk || state.disk,
      // FE-7: a received frame is fresh evidence the feed is alive.
      lastUpdated: Date.now(),
    }));
  },

  fetchHardwareMetrics: async () => {
    const reqId = ++_hwFetchReqId;
    try {
      const data = await getHardwareMetrics();
      if (reqId === _hwFetchReqId) {
        set({
          gpus: data.gpus || [],
          cpu: data.cpu || null,
          memory: data.memory || null,
          disk: data.disk || null,
          // FE-7: same for the REST fallback (initial load / WS gap).
          lastUpdated: Date.now(),
        });
      }
    } catch (error) {
      if (reqId === _hwFetchReqId) {
        console.error('Failed to fetch hardware metrics:', error);
      }
    }
  },

  fetchSoftware: async () => {
    const reqId = ++_swFetchReqId;
    try {
      const data = await getSoftwareInfo();
      if (reqId === _swFetchReqId) {
        set({ software: data });
      }
    } catch (error) {
      if (reqId === _swFetchReqId) {
        console.error('Failed to fetch software info:', error);
      }
    }
  },
}));
