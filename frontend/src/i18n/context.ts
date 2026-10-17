import { createContext, useContext } from 'react';

// Non-component i18n primitives live here so the barrel (index.tsx) only
// exports the I18nProvider component — fast refresh requires component-only
// files (react-refresh/only-export-components, audit 2026-10-17 A-4).

export type Lang = 'en' | 'zh';

export type Interp = Record<string, string | number>;

export function interpolate(text: string, vars?: Interp): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (m, k: string) => {
    if (!(k in vars)) return m;
    const v = vars[k];
    return v == null ? '' : String(v);
  });
}

export interface I18nContextValue {
  lang: Lang;
  t: (key: string, vars?: Interp) => string;
  setLang: (lang: Lang) => void;
  toggleLang: () => void;
}

export const I18nContext = createContext<I18nContextValue | null>(null);

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n must be used within I18nProvider');
  return ctx;
}
