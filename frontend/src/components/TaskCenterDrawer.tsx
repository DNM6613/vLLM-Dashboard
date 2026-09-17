import { useEffect, useRef } from 'react';
import { CheckCircle2, Download, Loader2, PanelRightClose, Square, X, XCircle } from 'lucide-react';
import { useI18n } from '../i18n';
import type { UseTasks } from '../hooks/useTasks';
import type { DeployTask } from '../api/deployment';

function durationSecs(task: DeployTask): number | null {
  if (!task.started_at || !task.finished_at) return null;
  const s = Math.round((new Date(task.finished_at).getTime() - new Date(task.started_at).getTime()) / 1000);
  return s >= 0 ? s : null;
}

interface TaskCenterDrawerProps {
  tasks: UseTasks;
}

const STATUS_DOT: Record<string, string> = {
  queued: 'bg-text-muted',
  running: 'bg-accent',
  awaiting_reboot: 'bg-warning',
  success: 'bg-success',
  failed: 'bg-danger',
  cancelled: 'bg-text-muted',
  interrupted: 'bg-warning',
};

export function TaskCenterDrawer({ tasks }: TaskCenterDrawerProps) {
  const { t } = useI18n();
  const logRef = useRef<HTMLDivElement | null>(null);
  const {
    tasks: list,
    selectedId,
    selectTask,
    logText,
    activeCount,
    drawerOpen,
    openDrawer,
    closeDrawer,
    cancelTaskById,
    downloadLog,
  } = tasks;

  const selected = list.find((x) => x.id === selectedId) ?? null;

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logText, selectedId]);

  return (
    <>
      {/* floating toggle */}
      <button
        onClick={() => (drawerOpen ? closeDrawer() : openDrawer())}
        className="fixed bottom-5 right-5 z-40 flex items-center gap-2 px-3.5 py-2 rounded-full bg-bg-card border border-border shadow-lg text-sm text-text hover:bg-bg-hover transition-colors"
        aria-label={t('Task center')}
      >
        {activeCount > 0 ? (
          <Loader2 className="w-4 h-4 text-accent animate-spin" />
        ) : (
          <span className={`w-2.5 h-2.5 rounded-full ${activeCount > 0 ? 'bg-accent' : 'bg-text-muted'}`} />
        )}
        {t('Task center')}
        {activeCount > 0 && (
          <span className="ml-1 px-1.5 py-0.5 rounded-full bg-accent text-bg text-[11px] font-semibold">{activeCount}</span>
        )}
        {drawerOpen ? <PanelRightClose className="w-3.5 h-3.5" /> : null}
      </button>

      {drawerOpen && (
        <aside className="fixed top-0 right-0 h-full w-full max-w-md z-30 bg-bg-card border-l border-border shadow-2xl flex flex-col">
          <header className="flex items-center justify-between px-4 py-3 border-b border-border">
            <h3 className="text-sm font-medium">{t('Task center')}</h3>
            <button onClick={closeDrawer} className="text-text-muted hover:text-text" aria-label={t('Close')}>
              <X className="w-4 h-4" />
            </button>
          </header>

          {/* task list */}
          <div className="flex-1 min-h-0 max-h-[40%] overflow-y-auto">
            {list.length === 0 ? (
              <div className="text-xs text-text-muted text-center py-8">{t('No deployment tasks yet.')}</div>
            ) : (
              <ul className="divide-y divide-border">
                {list.slice(0, 20).map((task) => (
                  <li key={task.id}>
                    <button
                      onClick={() => selectTask(task.id === selectedId ? null : task.id)}
                      className={`w-full flex items-center gap-2 px-4 py-2.5 text-left text-xs hover:bg-bg-hover/50 transition-colors ${
                        task.id === selectedId ? 'bg-bg-hover/60' : ''
                      }`}
                    >
                      {task.status === 'running' ? (
                        <Loader2 className="w-3.5 h-3.5 text-accent animate-spin shrink-0" />
                      ) : task.status === 'success' ? (
                        <CheckCircle2 className="w-3.5 h-3.5 text-success shrink-0" />
                      ) : task.status === 'failed' ? (
                        <XCircle className="w-3.5 h-3.5 text-danger shrink-0" />
                      ) : (
                        <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${STATUS_DOT[task.status] ?? 'bg-text-muted'}`} />
                      )}
                      <span className="flex-1 min-w-0">
                        <span className="block truncate text-text">{task.title}</span>
                        <span className="block text-text-muted text-[11px]">
                          {t(task.status)}
                          {durationSecs(task) != null ? ` · ${durationSecs(task)}s` : ''}
                        </span>
                      </span>
                      {(task.status === 'queued' || task.status === 'running') && (
                        <Square
                          className="w-3.5 h-3.5 text-text-muted hover:text-danger shrink-0"
                          aria-label={t('Cancel task')}
                          onClick={(e) => { e.stopPropagation(); cancelTaskById(task.id); }}
                        />
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* log viewer */}
          <div className="flex-1 min-h-0 border-t border-border flex flex-col">
            <div className="flex items-center justify-between px-4 py-2 border-b border-border">
              <span className="text-xs font-medium text-text-muted">
                {selected ? t('Task log: {title}', { title: selected.title }) : t('Task log')}
              </span>
              {selected && (
                <div className="flex items-center gap-2">
                  {(selected.status === 'queued' || selected.status === 'running') && (
                    <button onClick={() => cancelTaskById(selected.id)}
                      className="flex items-center gap-1 text-[11px] text-text-muted hover:text-danger">
                      <Square className="w-3 h-3" /> {t('Cancel task')}
                    </button>
                  )}
                  <button onClick={() => downloadLog()}
                    className="flex items-center gap-1 text-[11px] text-text-muted hover:text-text">
                    <Download className="w-3 h-3" /> {t('Download log')}
                  </button>
                </div>
              )}
            </div>
            <div ref={logRef} className="flex-1 overflow-y-auto p-3 font-mono text-[11px] leading-relaxed text-text whitespace-pre-wrap break-all">
              {logText || (
                <span className="text-text-muted">
                  {selected ? t('Loading log...') : t('Select a task to view its log.')}
                </span>
              )}
            </div>
          </div>
        </aside>
      )}
    </>
  );
}
