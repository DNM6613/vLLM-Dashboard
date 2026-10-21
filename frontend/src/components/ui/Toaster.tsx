import { useEffect, useState } from 'react';
import { dismissToast, subscribeToasts, type ToastItem } from './toast';

/**
 * Renders the toast queue fed by showToast() (audit 2026-10-20 P1-1: the
 * store existed and 21 call sites pushed into it, but nothing rendered —
 * every operation feedback was silently dropped).
 */
export function Toaster() {
  const [toasts, setToasts] = useState<readonly ToastItem[]>([]);

  useEffect(() => subscribeToasts(setToasts), []);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
      {toasts.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => dismissToast(t.id)}
          title="点击关闭"
          className={`rounded-lg border px-3 py-2 text-left text-sm shadow-md transition-colors ${
            t.type === 'error'
              ? 'border-danger/40 bg-danger/10 text-danger hover:bg-danger/20'
              : 'border-border bg-bg-card text-text hover:bg-bg-hover'
          }`}
        >
          {t.message}
        </button>
      ))}
    </div>
  );
}
