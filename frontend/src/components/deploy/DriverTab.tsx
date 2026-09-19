import { useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2, Minus, Power, Star, XCircle } from 'lucide-react';
import type { DeployTask, DriverList } from '../../api/deployment';
import { useI18n } from '../../i18n';
import { Button } from '../ui/Button';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { TagChip } from './TagChip';

// Finished outcomes stay visible for a day, then clear out on their own.
const FRESH_MS = 24 * 3600 * 1000;

function isFreshFinished(task: DeployTask): boolean {
  if (!task.finished_at) return true;
  const age = Date.now() - new Date(task.finished_at).getTime();
  return Number.isFinite(age) && age <= FRESH_MS;
}

function stepIcon(status: string) {
  if (status === 'done') return <CheckCircle2 className="w-3.5 h-3.5 text-success shrink-0" />;
  if (status === 'running') return <Loader2 className="w-3.5 h-3.5 text-accent animate-spin shrink-0" />;
  if (status === 'failed') return <XCircle className="w-3.5 h-3.5 text-danger shrink-0" />;
  if (status === 'cancelled' || status === 'skipped') return <Minus className="w-3.5 h-3.5 text-text-muted shrink-0" />;
  return <span className="w-3.5 h-3.5 rounded-full border border-text-muted/40 shrink-0" />;
}

function TaskPanel({ task }: { task: DeployTask }) {
  const { t } = useI18n();
  const active = task.status === 'queued' || task.status === 'running';
  const pkg = String(task.result?.package ?? '');
  const tone = task.status === 'failed'
    ? 'border-danger/40 bg-danger/10'
    : task.status === 'success'
      ? 'border-success/40 bg-success/10'
      : active
        ? 'border-accent/40 bg-accent/10'
        : 'border-border bg-bg-hover/30';
  const headIcon = task.status === 'failed' ? (
    <XCircle className="w-4 h-4 text-danger shrink-0" />
  ) : task.status === 'success' ? (
    <CheckCircle2 className="w-4 h-4 text-success shrink-0" />
  ) : active ? (
    <Loader2 className="w-4 h-4 text-accent animate-spin shrink-0" />
  ) : (
    <Minus className="w-4 h-4 text-text-muted shrink-0" />
  );
  const showSteps = active || task.status === 'failed';
  return (
    <div className={`border rounded-lg p-3 ${tone}`}>
      <div className="flex items-center gap-2">
        {headIcon}
        <span className="text-xs font-medium flex-1 min-w-0 truncate">
          {t('Driver task progress')}{pkg ? ` · ${pkg}` : ''}
        </span>
        <span className="text-[11px] text-text-muted shrink-0">{t(task.status)}</span>
      </div>
      {showSteps && (
        <ul className="mt-2 space-y-1">
          {task.steps.map((s, i) => (
            <li key={i} className="flex items-center gap-2 text-xs">
              {stepIcon(s.status)}
              <span className={s.status === 'pending' ? 'text-text-muted' : ''}>{t(s.name)}</span>
            </li>
          ))}
        </ul>
      )}
      {task.status === 'failed' && (
        <div className="mt-2 space-y-1">
          {task.error && <div className="text-[11px] font-mono text-danger break-all">{task.error}</div>}
          {task.suggestion && (
            <div className="text-[11px] text-text-muted">{t('Suggestion')}: {task.suggestion}</div>
          )}
        </div>
      )}
      {task.status === 'success' && pkg && (
        <div className="mt-2 text-xs text-text-muted">
          {t('Driver {pkg} installed and verified.', { pkg })}
        </div>
      )}
      {task.log_tail?.length ? (
        <pre className="mt-2 max-h-28 overflow-y-auto bg-bg rounded px-2 py-1.5 font-mono text-[11px] leading-relaxed text-text-muted whitespace-pre-wrap break-all">
          {task.log_tail.join('\n')}
        </pre>
      ) : null}
      {active && (
        <div className="mt-2 text-[11px] text-text-muted">{t('Full log: Task Center (bottom-right).')}</div>
      )}
    </div>
  );
}

interface DriverTabProps {
  data: DriverList | null;
  picked: string;
  onPick: (pkg: string) => void;
  onApply: (pkg: string) => Promise<boolean>;
  onReboot: () => Promise<boolean>;
  busy: boolean;
}

export function DriverTab({ data, picked, onPick, onApply, onReboot, busy }: DriverTabProps) {
  const { t } = useI18n();
  const [confirming, setConfirming] = useState(false);
  const [confirmingReboot, setConfirmingReboot] = useState(false);
  const [acting, setActing] = useState(false);

  if (!data) {
    return <div className="text-xs text-text-muted py-6 text-center">{t('Loading...')}</div>;
  }

  const pickedRow = data.drivers.find((d) => d.package === picked);
  const isInstalledPick = Boolean(pickedRow?.installed);
  const minWarn = pickedRow?.version != null && pickedRow.version < data.min_driver;
  const cudaFloor = pickedRow?.version != null ? data.driver_min_cuda[String(pickedRow.version)] : undefined;
  const switchWarn = Boolean(pickedRow && !pickedRow.installed);

  const currentLabel = data.current_driver
    ? t('Current driver: {version}', { version: data.current_driver })
    : t('Current driver: not installed');
  const applySummary = [
    currentLabel,
    t('Target driver: {pkg}', { pkg: picked }),
    t('Warning: the existing NVIDIA driver will be removed and the server must be rebooted; running vLLM services are stopped first.'),
  ].join('\n');

  const handleApply = async () => {
    setConfirming(false);
    setActing(true);
    await onApply(picked);
    setActing(false);
  };

  const handleReboot = async () => {
    setConfirmingReboot(false);
    setActing(true);
    await onReboot();
    setActing(false);
  };

  return (
    <div className="space-y-3">
      <div className="text-xs text-text-muted">
        {t('Detected GPU: {gpus}', { gpus: data.gpu_models.join(', ') || t('none') })}
      </div>

      {data.pending_task && (
        <div className="border border-warning/40 bg-warning/10 rounded-lg p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-warning mt-0.5 shrink-0" />
            <div className="flex-1">
              <div className="text-xs">{t('Driver {pkg} installed — reboot the server to activate it.', { pkg: data.pending_task.package })}</div>
              <div className="flex gap-2 mt-2">
                <Button size="sm" onClick={() => setConfirmingReboot(true)} disabled={acting || busy}>
                  <Power className="w-3 h-3" /> {t('Reboot now')}
                </Button>
                <Button size="sm" variant="secondary">{t('Reboot later')}</Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {data.active_task && data.active_task.status !== 'awaiting_reboot' &&
        (data.active_task.status === 'queued' || data.active_task.status === 'running' ||
          isFreshFinished(data.active_task)) && (
        <TaskPanel task={data.active_task} />
      )}

      <div className="border border-border rounded-lg overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-bg-hover text-text-muted text-left">
              <th className="px-3 py-2 w-8"></th>
              <th className="px-3 py-2 w-10"></th>
              <th className="px-3 py-2">{t('Driver package')}</th>
              <th className="px-3 py-2">{t('Tags')}</th>
            </tr>
          </thead>
          <tbody>
            {data.drivers.map((row) => (
              <tr
                key={row.package}
                className={`border-t border-border cursor-pointer ${picked === row.package ? 'bg-bg-hover/60' : 'hover:bg-bg-hover/30'}`}
                onClick={() => onPick(row.package)}
              >
                <td className="px-3 py-2">
                  <input
                    type="radio"
                    name="driver-pick"
                    checked={picked === row.package}
                    onChange={() => onPick(row.package)}
                    onClick={(e) => e.stopPropagation()}
                    className="accent-sky-400"
                    aria-label={row.package}
                  />
                </td>
                <td className="px-1 py-2">
                  <div className="flex items-center gap-1">
                    {row.installed && (
                      <CheckCircle2 className="w-3.5 h-3.5 text-success" aria-label={t('Installed')} />
                    )}
                    {row.recommended && (
                      <Star className="w-3.5 h-3.5 text-warning" aria-label={t('Recommended')} />
                    )}
                  </div>
                </td>
                <td className="px-3 py-2 font-mono">
                  {row.package}
                  {row.installed && <span className="ml-2 text-success text-[11px]">{t('Installed')}</span>}
                  {row.note && <div className="text-warning/80 font-sans mt-0.5">{t(row.note)}</div>}
                </td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1">
                    {row.tags.map((tag) => <TagChip key={tag.key} tag={tag} />)}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {minWarn && (
        <div className="flex items-center gap-1.5 text-xs text-danger">
          <AlertTriangle className="w-3.5 h-3.5" />
          {t('Driver version below the vLLM recommended minimum {min}', { min: data.min_driver })}
        </div>
      )}
      {cudaFloor && (
        <div className="flex items-center gap-1.5 text-xs text-warning">
          <AlertTriangle className="w-3.5 h-3.5" />
          {t('Driver {major} requires CUDA {floor}+ minimum — incompatible with older CUDA versions.', {
            major: pickedRow?.version ?? '', floor: cudaFloor,
          })}
        </div>
      )}
      {switchWarn && !minWarn && (
        <div className="flex items-center gap-1.5 text-xs text-text-muted">
          <AlertTriangle className="w-3.5 h-3.5" />
          {t('Applying will uninstall the current driver; a reboot is required to activate the new one.')}
        </div>
      )}

      <div className="flex items-center justify-between pt-1">
        <div className="text-xs text-text-muted">
          {picked && isInstalledPick ? t('This is the currently installed driver.') : ''}
        </div>
        <Button
          variant="primary"
          onClick={() => setConfirming(true)}
          disabled={!picked || busy || acting || isInstalledPick}
        >
          {t('Apply driver')}
        </Button>
      </div>

      {confirming && (
        <ConfirmDialog
          title={t('Apply driver {pkg}?', { pkg: picked })}
          description={applySummary}
          confirmLabel={t('Apply')}
          danger
          onConfirm={handleApply}
          onCancel={() => setConfirming(false)}
        />
      )}
      {confirmingReboot && (
        <ConfirmDialog
          title={t('Reboot the AI server now?')}
          description={t('The server will reboot to activate the new driver. All services are interrupted until it is back (1-3 minutes).')}
          confirmLabel={t('Reboot now')}
          danger
          onConfirm={handleReboot}
          onCancel={() => setConfirmingReboot(false)}
        />
      )}
    </div>
  );
}
