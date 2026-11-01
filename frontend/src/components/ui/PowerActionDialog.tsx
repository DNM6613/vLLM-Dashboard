import { useState } from 'react';
import { Power, PowerOff, RefreshCcw, RefreshCw, RotateCw } from 'lucide-react';
import { Modal } from './Modal';
import { Button } from './Button';
import { useI18n } from '../../i18n/context';
import type { PowerAction } from '../../types';

const ACTION_ICONS: Record<PowerAction, typeof Power> = {
  off: PowerOff,
  soft: Power,
  reset: RotateCw,
  cycle: RefreshCw,
  reboot: RefreshCcw,
};

const ACTION_DESCRIPTIONS: Record<PowerAction, string> = {
  off: 'Hard power off — immediate, the OS does not shut down cleanly.',
  soft: 'Graceful shutdown — the OS shuts down normally.',
  reset: 'Hard reboot — CPU reset, unsaved data is lost.',
  cycle: 'Power cycle — full power off then power on.',
  reboot: 'Graceful reboot — the OS restarts, power is not cut.',
};

// IPMI chassis verbs that need a BMC to issue. Without one they are
// unavailable and the SSH 'reboot' fallback is offered instead.
const IPMI_ONLY: PowerAction[] = ['off', 'reset', 'cycle'];

interface PowerActionDialogProps {
  defaultAction?: PowerAction;
  onConfirm: (action: PowerAction) => void;
  onCancel: () => void;
  /** Whether a BMC is configured. Without one the IPMI verbs (off/reset/
   *  cycle) are disabled and the SSH 'reboot' fallback row is added. */
  bmcConfigured?: boolean;
}

export function PowerActionDialog({
  defaultAction = 'soft', onConfirm, onCancel, bmcConfigured = true,
}: PowerActionDialogProps) {
  const { t } = useI18n();
  const isDisabled = (a: PowerAction) => !bmcConfigured && IPMI_ONLY.includes(a);
  const actions: PowerAction[] = bmcConfigured
    ? ['off', 'soft', 'reset', 'cycle']
    : ['off', 'soft', 'reset', 'cycle', 'reboot'];
  // 'soft' is enabled in both modes, so it is a safe fallback if the
  // requested default is an IPMI-only verb with no BMC.
  const [action, setAction] = useState<PowerAction>(
    isDisabled(defaultAction) ? 'soft' : defaultAction);

  const select = (a: PowerAction) => {
    if (!isDisabled(a)) setAction(a);
  };

  return (
    <Modal title={t('Power action?')} icon={<Power className="w-5 h-5" />} maxWidth="max-w-md" onClose={onCancel}>
      {!bmcConfigured && (
        <p className="text-xs text-warning mb-3">
          {t('Without a BMC there is no way to power the server back on remotely.')}
        </p>
      )}
      <div role="radiogroup" aria-label={t('Power action?')} className="space-y-2">
        {actions.map((a) => {
          const Icon = ACTION_ICONS[a];
          const selected = action === a;
          const disabled = isDisabled(a);
          return (
            <button
              key={a}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled}
              onClick={() => select(a)}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg border text-left transition-colors ${
                disabled
                  ? 'border-border opacity-50 cursor-not-allowed'
                  : selected ? 'border-accent bg-accent/10' : 'border-border hover:bg-bg-hover'
              }`}
            >
              <Icon className={`w-4 h-4 shrink-0 ${selected ? 'text-accent' : 'text-text-muted'}`} />
              <span className="font-mono text-sm">{a}</span>
              <span className="text-xs text-text-muted">
                {disabled ? t('BMC required') : t(ACTION_DESCRIPTIONS[a])}
              </span>
            </button>
          );
        })}
      </div>
      <div className="flex justify-end mt-4 gap-2">
        <Button onClick={onCancel}>{t('Cancel')}</Button>
        <Button variant="danger" onClick={() => onConfirm(action)} disabled={isDisabled(action)}>{action}</Button>
      </div>
    </Modal>
  );
}
