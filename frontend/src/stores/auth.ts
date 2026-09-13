import { create } from 'zustand';
import type { AuthStore } from '../types';

const AUTH_PROMPT_COOLDOWN_MS = 5000;

export const useAuthStore = create<AuthStore>((set, get) => ({
  authRequired: false,
  lastPromptAt: 0,
  requestAuth: () => {
    const now = Date.now();
    if (now - get().lastPromptAt < AUTH_PROMPT_COOLDOWN_MS) return;
    set({ authRequired: true, lastPromptAt: now });
  },
}));
