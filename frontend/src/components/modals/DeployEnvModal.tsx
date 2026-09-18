import { useEffect, useState } from 'react';
import {
  Box, History, Rocket, Wrench,
} from 'lucide-react';
import { useI18n } from '../../i18n';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { useDeployment } from '../../hooks/useDeployment';
import { PreflightCard } from '../deploy/PreflightCard';
import { RiskPanel } from '../deploy/RiskPanel';
import { MirrorPanel } from '../deploy/MirrorPanel';
import { DriverTab } from '../deploy/DriverTab';
import { CudaTab } from '../deploy/CudaTab';
import { VllmTab } from '../deploy/VllmTab';

type TabId = 'driver' | 'cuda' | 'vllm';

interface DeployEnvModalProps {
  onClose: () => void;
}

export function DeployEnvModal({ onClose }: DeployEnvModalProps) {
  const { t } = useI18n();
  const dep = useDeployment(true);
  const [activeTab, setActiveTab] = useState<TabId>('driver');
  const [driverPick, setDriverPick] = useState('');
  const [cudaPick, setCudaPick] = useState('');
  // Matches the backend default (system CUDA Toolkit = robust default; see
  // _default_state). The effect below resyncs from persisted state on load.
  const [cudaInstallSystem, setCudaInstallSystem] = useState(true);

  // Keep local tab selections in sync with persisted state (initial load).
  // Local radio clicks do not write state, so this does not fight the
  // user's in-progress selection.
  const selDriver = dep.state?.selected.driver ?? '';
  const selCuda = dep.state?.selected.cuda ?? '';
  useEffect(() => { if (selDriver) setDriverPick(selDriver); }, [selDriver]);
  useEffect(() => { if (selCuda) setCudaPick(selCuda); }, [selCuda]);
  useEffect(() => {
    if (dep.state) setCudaInstallSystem(Boolean(dep.state.selected.cuda_install_system));
  }, [dep.state?.selected.cuda_install_system]);

  const cudaLocked = Boolean(dep.state?.locks.cuda);

  const TABS: { id: TabId; label: string; locked: boolean }[] = [
    { id: 'driver', label: t('GPU Driver'), locked: false },
    { id: 'cuda', label: 'CUDA', locked: cudaLocked },
    { id: 'vllm', label: 'vLLM', locked: false },
  ];

  const snapshot = dep.state?.snapshots;

  return (
    <Modal title={t('Environment Deployment')} icon={<Rocket className="w-5 h-5" />} maxWidth="max-w-4xl" hideScrollbar onClose={onClose}
      footer={
        <>
          {snapshot?.driver.previous_pkg && (
            <Button size="sm" onClick={() => dep.handleRollback('driver')} disabled={dep.busy}
              title={t('Reinstall the previous driver: {pkg}', { pkg: snapshot.driver.previous_pkg })}>
              <History className="w-3 h-3" /> {t('Roll back driver')}
            </Button>
          )}
          {snapshot?.vllm.previous_version && (
            <Button size="sm" onClick={() => dep.handleRollback('vllm')} disabled={dep.busy}
              title={t('Reinstall the previous version: {ver}', { ver: snapshot.vllm.previous_version })}>
              <History className="w-3 h-3" /> {t('Roll back vLLM')}
            </Button>
          )}
          <Button onClick={onClose}>{t('Close')}</Button>
        </>
      }
    >
      <div className="space-y-4">
        {dep.error && (
          <div className="border border-danger/40 bg-danger/10 rounded-lg p-3 text-xs text-danger">{dep.error}</div>
        )}

        <PreflightCard preflight={dep.preflight} loading={dep.preflightLoading} onRefresh={dep.loadAll} />

        {/* tabs */}
        <div className="flex gap-1 border-b border-border">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-1.5 px-4 py-2 text-sm rounded-t-lg border border-b-0 transition-colors ${
                activeTab === tab.id
                  ? 'border-border bg-bg-card text-text'
                  : 'border-transparent text-text-muted hover:text-text'
              }`}
            >
              {tab.id === 'driver' ? <Box className="w-3.5 h-3.5" /> : tab.id === 'cuda' ? <CpuIcon /> : <Wrench className="w-3.5 h-3.5" />}
              {tab.label}
              {tab.locked && <span className="text-[10px] text-warning">{t('locked')}</span>}
            </button>
          ))}
        </div>

        {activeTab === 'driver' && (
          <DriverTab
            data={dep.drivers}
            picked={driverPick}
            onPick={setDriverPick}
            onApply={dep.handleApplyDriver}
            onReboot={dep.handleReboot}
            busy={dep.busy}
          />
        )}
        {activeTab === 'cuda' && (
          <CudaTab
            locked={cudaLocked}
            info={dep.cuda}
            pickedVersion={cudaPick}
            onPickVersion={setCudaPick}
            installSystem={cudaInstallSystem}
            onInstallSystemChange={setCudaInstallSystem}
            onApply={dep.handleCuda}
            busy={dep.busy}
          />
        )}
        {activeTab === 'vllm' && (
          <VllmTab
            info={dep.vllm}
            cudaVersion={cudaPick || (dep.state?.selected.cuda ?? '')}
            cudaInstallSystem={cudaInstallSystem}
            currentToolkit={dep.cuda?.current_toolkit ?? ''}
            pypiMirror={dep.state?.mirrors.pypi ?? ''}
            onApply={dep.handleVllm}
            busy={dep.busy}
          />
        )}

        <MirrorPanel
          pypi={dep.state?.mirrors.pypi ?? ''}
          hf={dep.state?.mirrors.hf ?? ''}
          saving={dep.busy}
          onSave={dep.handleMirrors}
        />

        {dep.state?.history.length ? (
          <section className="border border-border rounded-lg p-4">
            <div className="flex items-center gap-2 mb-2">
              <History className="w-4 h-4 text-accent" />
              <h4 className="text-sm font-medium">{t('Recent operations')}</h4>
            </div>
            <ul className="space-y-1 text-xs">
              {[...dep.state.history].reverse().slice(0, 5).map((entry, i) => (
                <li key={`${entry.ts}-${i}`} className="flex items-center gap-2">
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                    entry.status === 'success' ? 'bg-success' : entry.status === 'failed' ? 'bg-danger' : 'bg-text-muted'
                  }`} />
                  <span className="text-text-muted shrink-0">{entry.ts}</span>
                  <span className="truncate">{entry.title}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <RiskPanel />
      </div>
    </Modal>
  );
}

// Small inline CPU icon to avoid an extra lucide import name clash.
function CpuIcon() {
  return (
    <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <rect x="9" y="9" width="6" height="6" />
      <path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" />
    </svg>
  );
}
