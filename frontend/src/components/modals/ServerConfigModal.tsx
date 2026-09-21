import { useRef, useState, type ReactNode } from 'react';
import { Settings, Save, Loader2, Terminal, Globe, Power } from 'lucide-react';
import type { ServerConfig } from '../../types';
import { useI18n } from '../../i18n';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { ConfirmDialog } from '../ui/ConfirmDialog';

interface ServerConfigModalProps {
  config: ServerConfig;
  onConfigChange: (config: ServerConfig) => void;
  onSave: (payload?: Partial<ServerConfig>) => void;
  saving: boolean;
  message: { type: 'success' | 'error'; text: string } | null;
  onClose: () => void;
}

const CRED_FIELDS = ['ssh_password', 'bmc_password'] as const;
type CredField = (typeof CRED_FIELDS)[number];

const CRED_LABEL_KEY: Record<CredField, string> = {
  ssh_password: 'SSH Password',
  bmc_password: 'BMC Password',
};

const FIELD_CLASS = 'w-full px-3 py-1.5 bg-bg rounded-lg border border-border text-text focus:border-accent focus:outline-none font-mono text-sm';

export function ServerConfigModal({ config, onConfigChange, onSave, saving, message, onClose }: ServerConfigModalProps) {
  const { t } = useI18n();
  const [sshPortDraft, setSshPortDraft] = useState(String(config.ssh_port));
  const parsePort = (s: string): number | null => {
    if (!/^\d{1,5}$/.test(s)) return null;
    const n = parseInt(s, 10);
    return n >= 1 && n <= 65535 ? n : null;
  };
  const sshPort = parsePort(sshPortDraft);

  // Credentials as they were when the modal opened ("***" mask if the
  // value is stored server-side, "" if not). Used to detect that the user
  // cleared a saved value, so the clear can be confirmed before saving.
  const initialCreds = useRef<Record<CredField, string>>({
    ssh_password: config.ssh_password ?? '',
    bmc_password: config.bmc_password ?? '',
  });
  const [pendingClear, setPendingClear] = useState<{
    payload: Partial<ServerConfig>;
    fields: CredField[];
  } | null>(null);

  const buildPayload = (): Partial<ServerConfig> => {
    const payload: Partial<ServerConfig> = { ...config };
    for (const f of CRED_FIELDS) {
      if (payload[f] === undefined || payload[f] === null) delete payload[f];
    }
    return payload;
  };

  const handleSaveClick = () => {
    const payload = buildPayload();
    const cleared = CRED_FIELDS.filter(
      (f) => (initialCreds.current[f] ?? '') !== '' && (config[f] ?? '') === '',
    );
    if (cleared.length > 0) {
      setPendingClear({ payload, fields: cleared });
      return;
    }
    onSave(payload);
  };

  const renderCredField = (f: CredField, id: string, label: string, icon?: ReactNode) => (
    <div>
      <label htmlFor={id} className="block text-xs text-text-muted mb-1 flex items-center gap-1">{icon}{label}</label>
      <input
        id={id}
        type="password"
        value={config[f] ?? ''}
        onChange={(e) => { const next = { ...config }; next[f] = e.target.value; onConfigChange(next); }}
        onFocus={(e) => e.currentTarget.select()}
        onClick={(e) => e.currentTarget.select()}
        className={FIELD_CLASS}
      />
    </div>
  );

  return (
    <>
      <Modal
        title={t('Server Config')}
        icon={<Settings className="w-5 h-5" />}
        maxWidth="max-w-xl"
        onClose={onClose}
        footer={
          <>
            <Button onClick={onClose}>{t('Cancel')}</Button>
            <Button variant="primary" onClick={handleSaveClick} disabled={saving || sshPort === null}>
              {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />} {t('Save')}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <section className="border border-border rounded-lg p-4">
            <div className="flex items-center gap-2 mb-3">
              <Globe className="w-4 h-4 text-accent" />
              <h4 className="text-sm font-medium">{t('IP Address')}</h4>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="srv-host" className="block text-xs text-text-muted mb-1">{t('Server Address (IPv4)')}</label>
                <input id="srv-host" type="text" value={config.host} onChange={(e) => onConfigChange({ ...config, host: e.target.value })} className={FIELD_CLASS} />
              </div>
              <div>
                <label htmlFor="srv-bmc-host" className="block text-xs text-text-muted mb-1">{t('BMC Address (IPv4)')}</label>
                <input id="srv-bmc-host" type="text" value={config.bmc_host || ''} onChange={(e) => onConfigChange({ ...config, bmc_host: e.target.value })} className={FIELD_CLASS} placeholder={t('Optional')} />
              </div>
            </div>
          </section>
          <section className="border border-border rounded-lg p-4">
            <div className="flex items-center gap-2 mb-3">
              <Terminal className="w-4 h-4 text-accent" />
              <h4 className="text-sm font-medium">SSH</h4>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="srv-ssh-username" className="block text-xs text-text-muted mb-1">{t('SSH Username')}</label>
                <input id="srv-ssh-username" type="text" value={config.ssh_username} onChange={(e) => onConfigChange({ ...config, ssh_username: e.target.value })} className={FIELD_CLASS} />
              </div>
              {renderCredField('ssh_password', 'srv-ssh-password', t('SSH Password'))}
            </div>
            <div className="grid grid-cols-2 gap-3 mt-3">
              <div>
                <label htmlFor="srv-ssh-port" className="block text-xs text-text-muted mb-1">{t('SSH Port')}</label>
                <input id="srv-ssh-port" type="text" inputMode="numeric" value={sshPortDraft} onChange={(e) => { const v = e.target.value.replace(/\D/g, ''); setSshPortDraft(v); const p = parsePort(v); if (p !== null) onConfigChange({ ...config, ssh_port: p }); }} className={FIELD_CLASS} placeholder="22" />
              </div>
              <div title={t('activated before download/run: source ~/{venv}/bin/activate', { venv: config.venv_name })}>
                <label htmlFor="srv-venv-name" className="block text-xs text-text-muted mb-1">{t('Python Venv Directory')}</label>
                <input id="srv-venv-name" type="text" value={config.venv_name} onChange={(e) => onConfigChange({ ...config, venv_name: e.target.value })} className={FIELD_CLASS} placeholder=".venv" />
              </div>
            </div>
          </section>
          <section className="border border-border rounded-lg p-4">
            <div className="flex items-center gap-2 mb-3">
              <Power className="w-4 h-4 text-accent" />
              <h4 className="text-sm font-medium">BMC</h4>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="srv-bmc-username" className="block text-xs text-text-muted mb-1">{t('BMC Username')}</label>
                <input id="srv-bmc-username" type="text" value={config.bmc_username || ''} onChange={(e) => onConfigChange({ ...config, bmc_username: e.target.value })} className={FIELD_CLASS} />
              </div>
              {renderCredField('bmc_password', 'srv-bmc-password', t('BMC Password'))}
            </div>
          </section>
        </div>
        {message && (
          <div className={`mt-3 text-xs ${message.type === 'success' ? 'text-success' : 'text-danger'}`}>{message.text}</div>
        )}
      </Modal>
      {pendingClear && (
        <ConfirmDialog
          title={t('Clear saved credentials?')}
          description={t('The saved values for the following will be erased — you will need to enter them again: {fields}', {
            fields: pendingClear.fields.map((f) => t(CRED_LABEL_KEY[f])).join(', '),
          })}
          confirmLabel={t('Confirm')}
          danger
          onConfirm={() => {
            const payload = pendingClear.payload;
            setPendingClear(null);
            onSave(payload);
          }}
          onCancel={() => setPendingClear(null)}
        />
      )}
    </>
  );
}
