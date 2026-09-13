import { type ReactNode, useEffect, useState } from 'react';
import { AlertCircle, Info, X } from 'lucide-react';
import { dismissToast, subscribeToasts, type ToastItem } from './toast';
import { useI18n } from '../../i18n';

export function TopStack({ children }: { children?: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const { t } = useI18n();

  useEffect(() => subscribeToasts((toasts) => setToasts([...toasts])), []);

  return (
    <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-2 w-[min(90vw,42rem)] pointer-events-none">
      {children}
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role="alert"
          className={`top-stack-item pointer-events-auto flex items-start gap-2 bg-bg-card rounded-lg px-4 py-2 shadow-lg border max-w-full ${
            toast.type === 'error' ? 'border-danger/40' : 'border-accent/40'
          }`}
        >
          {toast.type === 'error'
            ? <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
            : <Info className="w-4 h-4 text-accent shrink-0 mt-0.5" />}
          <span className={`text-xs font-mono whitespace-pre-line break-words ${toast.type === 'error' ? 'text-danger' : 'text-text'}`}>
            {toast.message}
          </span>
          <button
            onClick={() => dismissToast(toast.id)}
            aria-label={t('Close')}
            className="p-1 rounded hover:bg-bg-hover transition-colors text-text-muted shrink-0"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
