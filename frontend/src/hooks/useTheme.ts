import { useCallback, useEffect, useState } from 'react';

export type Theme = 'dark' | 'light';

const THEME_KEY = 'vllm_theme';

export function getStoredTheme(): Theme {
  try {
    return localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

let current: Theme = getStoredTheme();
const listeners = new Set<() => void>();

export function getCurrentTheme(): Theme {
  return current;
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('light', theme === 'light');
}

function commitTheme(next: Theme): void {
  if (next === current) return;
  current = next;
  applyTheme(current);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {  }
  for (const listener of listeners) listener();
}

export function useTheme(): { theme: Theme; toggleTheme: () => void } {
  const [theme, setTheme] = useState<Theme>(current);

  useEffect(() => {
    const listener = () => setTheme(current);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  const toggleTheme = useCallback(() => {
    commitTheme(current === 'dark' ? 'light' : 'dark');
  }, []);

  return { theme, toggleTheme };
}
