import { create } from 'zustand';

interface ConsoleStore {
  connected: boolean;
  setConnected: (connected: boolean) => void;
}

export const useConsoleStore = create<ConsoleStore>((set) => ({
  connected: false,
  setConnected: (connected) => set({ connected }),
}));
