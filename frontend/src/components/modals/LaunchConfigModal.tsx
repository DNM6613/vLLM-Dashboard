import { Settings, Save, Loader2 } from 'lucide-react';
import { useLayoutEffect, useRef, type RefObject } from 'react';
import { useI18n } from '../../i18n';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';

interface LaunchConfigModalProps {
  modelName: string;
  modelPath: string;
  command: string;
  onCommandChange: (value: string) => void;
  envVars: string;
  onEnvVarsChange: (value: string) => void;
  onSave: () => void;
  saving: boolean;
  onClose: () => void;
}

const FIELD_CLASS = 'w-full px-3 py-1.5 bg-bg rounded-lg border border-border text-text focus:border-accent focus:outline-none font-mono text-sm';

// Height = content + one line of headroom, so no scrollbar is ever needed.
function useAutoResize(ref: RefObject<HTMLTextAreaElement | null>, value: string) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight);
    el.style.height = `${el.scrollHeight + (Number.isFinite(lineHeight) ? lineHeight : 20)}px`;
  }, [ref, value]);
}

export function LaunchConfigModal({ modelName, modelPath, command, onCommandChange, envVars, onEnvVarsChange, onSave, saving, onClose }: LaunchConfigModalProps) {
  const { t } = useI18n();
  const envVarsRef = useRef<HTMLTextAreaElement | null>(null);
  const commandRef = useRef<HTMLTextAreaElement | null>(null);
  useAutoResize(envVarsRef, envVars);
  useAutoResize(commandRef, command);
  return (
    <Modal
      title={t('Launch Config: {name}', { name: modelName })}
      icon={<Settings className="w-5 h-5" />}
      maxWidth="max-w-xl"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{t('Cancel')}</Button>
          <Button variant="primary" onClick={onSave} disabled={saving}>
            {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />} {t('Save')}
          </Button>
        </>
      }
    >
      <div>
        <span className="block text-xs text-text-muted mb-1">{t('Model Path')}</span>
        <div className="w-full px-3 py-1.5 bg-bg rounded-lg border border-border text-text-muted font-mono text-xs mb-3 break-all">{modelPath || '-'}</div>
      </div>
      <div>
        <label htmlFor="lc-env-vars" className="block text-xs text-text-muted mb-1">{t('Environment Variables')}</label>
        <textarea
          id="lc-env-vars"
          ref={envVarsRef}
          value={envVars}
          onChange={(e) => onEnvVarsChange(e.target.value)}
          className={`${FIELD_CLASS} no-scrollbar mb-3`}
          placeholder={t('# KEY=VALUE per line (export prefix ok), applied before the start command\nHF_ENDPOINT=https://hf-mirror.com')}
        />
      </div>
      <div>
        <label htmlFor="lc-start-command" className="block text-xs text-text-muted mb-1">{t('Start Command')}</label>
        <textarea
          id="lc-start-command"
          ref={commandRef}
          value={command}
          onChange={(e) => onCommandChange(e.target.value)}
          className={`${FIELD_CLASS} no-scrollbar`}
          placeholder="vllm serve /path/to/model --max-model-len 32768 --gpu-memory-utilization 0.92"
        />
      </div>
    </Modal>
  );
}
