import { Power, Loader2, Unplug } from 'lucide-react';
import { useI18n } from '../i18n';

interface ServerOffOverlayProps {
  starting?: boolean;
  bmcConfigured?: boolean;
}

export function ServerOffOverlay({ starting = false, bmcConfigured = true }: ServerOffOverlayProps) {
  const { t } = useI18n();
  const message = starting
    ? t('Server is starting up')
    : (!bmcConfigured ? t('Server SSH not connected') : t('Server is powered off'));
  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div
        role="status"
        className="flex items-center gap-3 rounded-xl border border-border bg-bg-card/70 px-8 py-6 shadow-lg backdrop-blur-sm"
      >
        {starting ? (
          <Loader2 className="h-6 w-6 text-text-muted animate-spin" />
        ) : (!bmcConfigured ? (
          <Unplug className="h-6 w-6 text-text-muted" />
        ) : (
          <Power className="h-6 w-6 text-text-muted" />
        ))}
        <span className="text-lg font-medium">{message}</span>
      </div>
    </div>
  );
}
