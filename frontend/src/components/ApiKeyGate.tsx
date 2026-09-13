import { useState } from 'react';
import { KeyRound, Loader2 } from 'lucide-react';
import { useAuthStore } from '../stores/auth';
import { useI18n } from '../i18n';
import { Button } from './ui/Button';
import { getStoredApiKey, setStoredApiKey } from '../utils/apiKey';
import { getDashboardHealth } from '../api/serverConfig';

export function ApiKeyGate() {
  const { t } = useI18n();
  const authRequired = useAuthStore((s) => s.authRequired);
  const [key, setKey] = useState(getStoredApiKey);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  if (!authRequired) return null;

  const handleConfirm = async () => {
    if (!key.trim()) return;
    setChecking(true);
    setError(null);
    setStoredApiKey(key.trim());
    try {
      await getDashboardHealth();
      window.location.reload();
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      setError(status === 401 ? t('Invalid API key') : t('Verification failed'));
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-[min(90vw,26rem)] rounded-xl border border-border bg-bg-card/70 p-6 shadow-lg backdrop-blur-sm">
        <div className="flex items-center gap-3 mb-3">
          <KeyRound className="h-6 w-6 text-accent" />
          <h3 className="text-lg font-medium">{t('API Key Required')}</h3>
        </div>
        <p className="text-sm text-text-muted mb-4">{t('The server requires an API key to access. Enter the API key to continue.')}</p>
        <form
          onSubmit={(e) => { e.preventDefault(); void handleConfirm(); }}
          className="space-y-3"
        >
          <input
            autoFocus
            type="password"
            value={key}
            onChange={(e) => { setKey(e.target.value); setError(null); }}
            placeholder={t('API Key')}
            aria-label={t('API Key')}
            className="w-full px-3 py-2 bg-bg rounded-lg border border-border text-text focus:border-accent focus:outline-none font-mono text-sm"
          />
          {error && <p className="text-xs text-danger">{error}</p>}
          <div className="flex justify-end">
            <Button
              type="submit"
              variant="primary"
              disabled={checking || !key.trim()}
            >
              {checking ? <Loader2 className="w-3 h-3 animate-spin" /> : <KeyRound className="w-3 h-3" />}
              {t('Confirm')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
