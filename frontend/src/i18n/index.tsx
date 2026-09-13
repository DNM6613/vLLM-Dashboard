import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { LANG_KEY, readStoredLang, ZH } from './strings';

export type Lang = 'en' | 'zh';

export type Interp = Record<string, string | number>;

function interpolate(text: string, vars?: Interp): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m));
}

interface I18nContextValue {
  lang: Lang;
  t: (key: string, vars?: Interp) => string;
  setLang: (lang: Lang) => void;
  toggleLang: () => void;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(readStoredLang);

  useEffect(() => {
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      localStorage.setItem(LANG_KEY, next);
    } catch {  }
  }, []);

  const toggleLang = useCallback(() => {
    setLangState((prev) => {
      const next: Lang = prev === 'en' ? 'zh' : 'en';
      try {
        localStorage.setItem(LANG_KEY, next);
      } catch {  }
      return next;
    });
  }, []);

  const t = useCallback(
    (key: string, vars?: Interp) => interpolate(lang === 'zh' ? ZH[key] ?? key : key, vars),
    [lang],
  );

  const value = useMemo<I18nContextValue>(() => ({ lang, t, setLang, toggleLang }), [lang, t, setLang, toggleLang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n must be used within I18nProvider');
  return ctx;
}
