import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { I18nContext, interpolate, type I18nContextValue, type Interp, type Lang } from './context';
import { LANG_KEY, readStoredLang, ZH } from './strings';

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

// Type-only re-exports keep existing import sites working; they produce no
// runtime exports, so this barrel stays component-only for fast refresh.
export type { Interp, I18nContextValue, Lang } from './context';
