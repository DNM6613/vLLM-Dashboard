import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, XCircle } from 'lucide-react';
import type { Preflight } from '../../api/deployment';
import { useI18n, type Interp } from '../../i18n';

interface PreflightCardProps {
  preflight: Preflight | null;
  loading: boolean;
  onRefresh: () => void;
}

// Values embed live data (GB number, host list, kernel/OS version), so a
// whole-string lookup would never match — translate the static frame via a
// template and keep the data; plain values fall through to a direct lookup.
export function displayPreflightValue(
  item: { key: string; value: string },
  t: (key: string, vars?: Interp) => string,
): string {
  const v = item.value;
  if (item.key === 'disk' && /^[\d.]+ GB free$/.test(v)) {
    return t('{n} GB free', { n: v.replace(' GB free', '') });
  }
  if (item.key === 'network' && v.startsWith('reachable: ')) {
    return t('reachable: {hosts}', { hosts: v.slice('reachable: '.length) });
  }
  return t(v);
}

const STATUS_STYLE = {
  ok: 'text-success',
  warn: 'text-warning',
  fail: 'text-danger',
} as const;

export function PreflightCard({ preflight, loading, onRefresh }: PreflightCardProps) {
  const { t } = useI18n();

  return (
    <section className="border border-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-medium">{t('Pre-flight Check')}</h4>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="flex items-center gap-1 text-xs text-text-muted hover:text-text transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
          {t('Re-check')}
        </button>
      </div>
      {loading && !preflight ? (
        <div className="flex items-center gap-2 text-xs text-text-muted py-2">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t('Checking environment...')}
        </div>
      ) : preflight ? (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1.5">
            {preflight.items.map((item) => (
              <div key={item.key} className="flex items-start gap-1.5 text-xs" title={item.detail ? t(item.detail) : undefined}>
                {item.status === 'ok' ? (
                  <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0 text-success" />
                ) : item.status === 'warn' ? (
                  <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0 text-warning" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0 text-danger" />
                )}
                <span className="text-text-muted shrink-0 w-24 truncate">{t(item.label)}</span>
                <span className={`${STATUS_STYLE[item.status]} truncate`}>{displayPreflightValue(item, t)}</span>
              </div>
            ))}
          </div>
          {preflight.blocking && (
            <div className="mt-2 text-xs text-danger flex items-center gap-1">
              <AlertTriangle className="w-3.5 h-3.5" />
              {t('Some required checks failed — the affected tabs are blocked until fixed.')}
            </div>
          )}
        </>
      ) : null}
    </section>
  );
}
