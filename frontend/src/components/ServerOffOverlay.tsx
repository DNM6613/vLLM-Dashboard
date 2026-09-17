import { Power, Loader2, Unplug } from 'lucide-react';
import { useI18n } from '../i18n';

type PowerState = 'starting' | 'off' | 'unknown';

interface ServerOffOverlayProps {
  state: PowerState;
}

export function ServerOffOverlay({ state }: ServerOffOverlayProps) {
  const { t } = useI18n();
  const message =
    state === 'starting' ? t('Server is starting up')
    : state === 'off' ? t('Server is powered off')
    : t('Server SSH not connected');
  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div
        role="status"
        className="flex items-center gap-3 rounded-xl border border-border bg-bg-card/70 px-8 py-6 shadow-lg backdrop-blur-sm"
      >
        {state === 'starting' ? (
          <Loader2 className="h-6 w-6 text-text-muted animate-spin" />
        ) : (
          state === 'off' ? (
            <Power className="h-6 w-6 text-text-muted" />
          ) : (
            <Unplug className="h-6 w-6 text-text-muted" />
          )
        )}
        <span className="text-lg font-medium">{message}</span>
      </div>
    </div>
  );
}
