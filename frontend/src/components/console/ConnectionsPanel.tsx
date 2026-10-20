import { memo, useCallback, useEffect, useState } from 'react';
import { FileText } from 'lucide-react';
import { getConnections, getRequests } from '../../api/monitor';
import type { ConnectionInfo, RequestEntry } from '../../api/monitor';
import { useI18n } from '../../i18n/context';
import type { Interp } from '../../i18n/context';
import { Modal } from '../ui/Modal';

const POLL_INTERVAL_MS = 5000;

function relativeTime(ts: number, now: number, t: (key: string, vars?: Interp) => string): string {
  const diff = Math.max(0, Math.floor((now - ts * 1000) / 1000));
  if (diff < 5) return t('Just now');
  if (diff < 60) return t('{n}s ago', { n: diff });
  if (diff < 3600) return t('{n}m ago', { n: Math.floor(diff / 60) });
  return t('{n}h ago', { n: Math.floor(diff / 3600) });
}

export const ConnectionsPanel = memo(function ConnectionsPanel() {
  const { t } = useI18n();
  const [connections, setConnections] = useState<ConnectionInfo[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [detailIp, setDetailIp] = useState<string | null>(null);
  const [entries, setEntries] = useState<RequestEntry[]>([]);
  const [detailOpen, setDetailOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const rows = await getConnections();
        if (!cancelled) {
          setConnections(rows);
          setNow(Date.now());
        }
      } catch {
        // Keep the last good data when a poll hiccups.
      }
    };
    load();
    const timer = setInterval(load, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const openDetails = useCallback(async (ip: string) => {
    setDetailIp(ip);
    setEntries([]);
    setDetailOpen(true);
    try {
      setEntries(await getRequests(ip));
    } catch {
      // Leave the list empty on a fetch failure.
    }
  }, []);

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 min-h-0 overflow-y-auto">
        {connections.length === 0 ? (
          <div className="h-full flex items-center justify-center text-sm text-text-muted">
            {t('No requests yet')}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-bg-card">
              <tr className="text-left text-xs text-text-muted">
                <th className="px-3 py-1 font-normal">IP</th>
                <th className="px-2 py-1 font-normal">{t('Requests')}</th>
                <th className="px-2 py-1 font-normal">{t('Last seen')}</th>
                <th className="px-2 py-1" />
              </tr>
            </thead>
            <tbody>
              {connections.map((c) => (
                <tr key={c.ip} className="border-t border-border/50">
                  <td className="px-3 py-1 font-mono">{c.ip}</td>
                  <td className="px-2 py-1 font-mono tabular-nums">{c.count}</td>
                  <td className="px-2 py-1 text-text-muted whitespace-nowrap">{relativeTime(c.last_seen, now, t)}</td>
                  <td className="px-2 py-1 text-right">
                    <button
                      type="button"
                      onClick={() => void openDetails(c.ip)}
                      className="rounded-lg px-2 py-0.5 text-xs text-accent hover:bg-bg-hover transition-colors"
                    >
                      {t('Details')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {detailOpen && detailIp && (
        <Modal
          title={t('Request details ({ip})', { ip: detailIp })}
          icon={<FileText className="w-5 h-5" />}
          maxWidth="max-w-2xl"
          onClose={() => setDetailOpen(false)}
        >
          {entries.length === 0 ? (
            <div className="py-6 text-center text-sm text-text-muted">{t('No requests yet')}</div>
          ) : (
            <div className="space-y-2 max-h-[65vh] overflow-y-auto pr-1">
              {entries.map((e, i) => (
                <div key={`${e.ts}-${i}`} className="rounded-lg border border-border p-2">
                  <div className="flex items-center gap-2 text-sm font-mono">
                    <span className="text-text-muted whitespace-nowrap">{new Date(e.ts * 1000).toLocaleTimeString()}</span>
                    <span className="font-medium">{e.method}</span>
                    <span className="truncate">{e.path}</span>
                    <span className={`ml-auto tabular-nums ${e.status < 400 ? 'text-success' : 'text-danger'}`}>{e.status}</span>
                  </div>
                  {e.body && (
                    <div className="mt-1">
                      <div className="text-xs text-text-muted">{t('Request body')}</div>
                      <pre className="rounded bg-[rgb(var(--c-console))] p-2 text-sm font-mono whitespace-pre-wrap break-words max-h-40 overflow-y-auto">{e.body}</pre>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
});

ConnectionsPanel.displayName = 'ConnectionsPanel';
