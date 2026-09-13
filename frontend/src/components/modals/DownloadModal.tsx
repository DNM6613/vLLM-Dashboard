import { Folder, Loader2 } from 'lucide-react';
import type { CliStatus } from '../../types';
import { useI18n } from '../../i18n';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';

interface DownloadModalProps {
  modelName: string;
  onModelNameChange: (value: string) => void;
  savePath: string;
  onSavePathChange: (value: string) => void;
  hfMirror: boolean;
  onHfMirrorChange: (value: boolean) => void;
  checkingCli: boolean;
  cliStatus: CliStatus | null;
  installingCli: boolean;
  installMessage: string;
  onInstallCli: (tool: 'hf') => void;
  onDownload: () => void;
  onClose: () => void;
}

const FIELD_CLASS = 'w-full px-3 py-1.5 bg-bg rounded-lg border border-border text-text focus:border-accent focus:outline-none font-mono text-sm';

export function DownloadModal({
  modelName, onModelNameChange,
  savePath, onSavePathChange, hfMirror, onHfMirrorChange,
  checkingCli, cliStatus, installingCli,
  installMessage, onInstallCli, onDownload, onClose,
}: DownloadModalProps) {
  const { t } = useI18n();
  return (
    <Modal
      title={t('Download Model')}
      icon={<Folder className="w-5 h-5" />}
      maxWidth="max-w-md"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{t('Cancel')}</Button>
          <Button variant="primary" onClick={onDownload} disabled={installingCli || !modelName.trim()}>{t('Download')}</Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="text-xs">
          {checkingCli && <span className="text-text-muted flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> {t('Checking CLI tools...')}</span>}
          {!checkingCli && cliStatus && (
            (() => {
              if (cliStatus.hf_installed) {
                return <span className="text-success flex items-center gap-1"><span className="text-xs">✓</span> {t('hf is installed')}</span>;
              }
              return (
                <div className="flex items-center gap-2">
                  <span className="text-warning flex items-center gap-1">
                    <span className="text-xs">⚠</span> {t('hf (huggingface_hub) not found')}
                  </span>
                  <Button size="sm" variant="primary" onClick={() => onInstallCli('hf')} disabled={installingCli}>
                    {installingCli ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                    {installingCli ? t('Installing...') : t('Install')}
                  </Button>
                </div>
              );
            })()
          )}
          {installMessage && <div className="text-text-muted mt-1">{installMessage}</div>}
        </div>
        <div>
          <label htmlFor="dl-model-repo" className="block text-xs text-text-muted mb-1">{t('Model Repo (e.g., Qwen/Qwen2-7B)')}</label>
          <input id="dl-model-repo" type="text" value={modelName} onChange={(e) => onModelNameChange(e.target.value)} className={FIELD_CLASS} />
        </div>
        <div>
          <label htmlFor="dl-save-path" className="block text-xs text-text-muted mb-1">{t('Model Save Path')}</label>
          <input id="dl-save-path" type="text" value={savePath} onChange={(e) => onSavePathChange(e.target.value)} className={FIELD_CLASS} />
          <p className="text-[11px] text-text-muted mt-1">{t("Must be under the remote user's HOME directory (~ is supported). Shell metacharacters are not allowed.")}</p>
          <p className="text-[11px] text-text-muted mt-0.5">{t('Default when left empty: ~/.cache/huggingface/hub/')}</p>
        </div>
        <div>
          <label className="block text-xs text-text-muted mb-1">{t('HF Mirror')}</label>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-1.5 text-sm cursor-pointer">
              <input type="radio" name="hf-mirror" checked={hfMirror} onChange={() => onHfMirrorChange(true)} className="accent-accent" />
              <span>{t('Domestic (hf-mirror.com)')}</span>
            </label>
            <label className="flex items-center gap-1.5 text-sm cursor-pointer">
              <input type="radio" name="hf-mirror" checked={!hfMirror} onChange={() => onHfMirrorChange(false)} className="accent-accent" />
              <span>{t('Official (huggingface.co)')}</span>
            </label>
          </div>
        </div>
      </div>
    </Modal>
  );
}
