import { useCallback, useEffect, useRef, useState } from 'react';
import { Settings, Power, Loader2, Sun, Moon, X } from 'lucide-react';
import { useI18n } from '../i18n';
import { useTheme } from '../hooks/useTheme';
import { invalidateHardwareFetch, invalidateSoftwareFetch, useHardwareStore } from '../stores/hardware';
import { useModelStore } from '../stores/model';
import { useServerConfig } from '../hooks/useServerConfig';
import { useHardwareWebSocket } from '../hooks/useHardwareWebSocket';
import { useModelStatusStore } from '../stores/modelStatus';
import { useConsoleWebSocket } from '../hooks/useConsoleWebSocket';
import { useModelManager } from '../hooks/useModelManager';
import { useModelDownload } from '../hooks/useModelDownload';
import {
  autoBenchAction,
  clearAutoBenchPending,
  readAutoBenchPending,
  saveAutoBenchPending,
  useBenchmark,
} from '../hooks/useBenchmark';
import { HardwareCards } from '../components/hardware/HardwareCards';
import { SoftwareVersionBadges } from '../components/hardware/SoftwareVersionBadges';
import { ConsolePanel } from '../components/console/ConsolePanel';
import { ModelList } from '../components/model/ModelList';
import { ModelStatusCard } from '../components/model/ModelStatusCard';
import { ServerConfigModal } from '../components/modals/ServerConfigModal';
import { DownloadModal } from '../components/modals/DownloadModal';
import { LaunchConfigModal } from '../components/modals/LaunchConfigModal';
import { BenchmarkModal } from '../components/modals/BenchmarkModal';
import { ServerOffOverlay } from '../components/ServerOffOverlay';
import { terminalReset } from '../utils/terminalSink';
import { useSettledFlag } from '../hooks/useSettledFlag';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { IconButton } from '../components/ui/IconButton';
import { TopStack } from '../components/ui/TopStack';

export function HomePage() {
  const { t, lang, toggleLang } = useI18n();
  const { theme, toggleTheme } = useTheme();

  const { serverConfig, setServerConfig, savingConfig, configMessage, showServerConfig, setShowServerConfig, sshConnected, apiConnected, bmcStatus, serverVersion, shuttingDown, shutdownRequested, requestShutdownServer, confirmShutdownServer, cancelShutdownServer, powerOning, handlePowerOnServer, handleSaveConfig } = useServerConfig();

  const fetchHardwareMetrics = useHardwareStore(s => s.fetchHardwareMetrics);
  const fetchSoftware = useHardwareStore(s => s.fetchSoftware);
  useHardwareWebSocket();

  const fetchModelStatus = useModelStatusStore(s => s.fetchModelStatus);

  useEffect(() => {
    if (sshConnected) return;
    invalidateHardwareFetch();
    invalidateSoftwareFetch();
    useHardwareStore.setState({ gpus: [], cpu: null, memory: null, disk: null, software: null });
  }, [sshConnected]);

  useEffect(() => {
    if (!sshConnected) return;
    fetchSoftware().catch((err) => { console.error('Software info error:', err); });
  }, [sshConnected, fetchSoftware]);

  const { models, loading, error: modelError } = useModelStore();

  const { benchmarkResults, benchmarkingIds, benchmarkErrors, handleBenchmark, showBenchmarkModal, setShowBenchmarkModal } = useBenchmark();

  const { connectConsole, startConsoleForModel, sendConsoleInput, sendConsoleResize } = useConsoleWebSocket();
  const { refreshing, scanMessage, starting, showLaunchConfigModal, setShowLaunchConfigModal, launchConfigModelName, launchConfigModelPath, launchCommand, setLaunchCommand, launchEnvVars, setLaunchEnvVars, savingLaunchConfig, handleRefresh, handleStartModel, handleStopModel, handleOpenLaunchConfig, handleSaveLaunchConfig, handleDeleteModel, deletingId, stoppingId, deleteRequest, confirmDelete, cancelDelete, stopRequest, confirmStop, cancelStop } = useModelManager({ startConsoleForModel, sshConnected, connectConsole });
  const { showDownloadModal, setShowDownloadModal, downloadModelName, setDownloadModelName, downloadModelSavePath, setDownloadModelSavePath, hfMirror, setHfMirror, downloading, downloadProgress, downloadNotice, clearDownloadNotice, activeDownloadName, cliStatus, checkingCli, installingCli, installMessage, handleInstallCli, handleDownloadModel } = useModelDownload({ defaultSavePath: serverConfig.model_save_path ?? '', onSavePathPersisted: (path: string) => setServerConfig((c) => ({ ...c, model_save_path: path })) });

  const openDownloadModal = useCallback(() => setShowDownloadModal(true), [setShowDownloadModal]);
  const openBenchmarkModal = useCallback(() => setShowBenchmarkModal(true), [setShowBenchmarkModal]);

  const handleStartWithAutoBench = useCallback(async (modelId: string) => {
    const ok = await handleStartModel(modelId);
    if (ok) saveAutoBenchPending(modelId);
  }, [handleStartModel]);
  useEffect(() => {
    const pending = readAutoBenchPending();
    if (!pending) return;
    const model = models.find(m => m.id === pending);
    const action = autoBenchAction(model?.status, benchmarkingIds.has(pending));
    if (action !== 'fire') return;
    clearAutoBenchPending();
    void (async () => {
      await handleBenchmark(pending);
      await handleBenchmark(pending);
    })();
  }, [models, handleBenchmark, benchmarkingIds]);

  const serverOn = sshConnected || apiConnected;

  const serverOff = !serverOn;
  const powerOffShown = useSettledFlag(serverOff, 2000);

  useEffect(() => {
    if (!powerOffShown) return;
    terminalReset();
  }, [powerOffShown]);

  const [powerOnPending, setPowerOnPending] = useState(false);
  const powerOnTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearPowerOnTimeout = useCallback(() => {
    if (powerOnTimeoutRef.current) {
      clearTimeout(powerOnTimeoutRef.current);
      powerOnTimeoutRef.current = null;
    }
  }, []);
  const handlePowerOnClick = useCallback(async () => {
    setPowerOnPending(true);
    clearPowerOnTimeout();
    powerOnTimeoutRef.current = setTimeout(() => {
      powerOnTimeoutRef.current = null;
      setPowerOnPending(false);
    }, 10 * 60 * 1000);
    try {
      await handlePowerOnServer();
    } catch {
      clearPowerOnTimeout();
      setPowerOnPending(false);
    }
  }, [handlePowerOnServer, clearPowerOnTimeout]);
  useEffect(() => {
    if (serverOn) {
      clearPowerOnTimeout();
      setPowerOnPending(false);
    }
  }, [serverOn, clearPowerOnTimeout]);
  useEffect(() => clearPowerOnTimeout, [clearPowerOnTimeout]);

  useEffect(() => {
    fetchHardwareMetrics().catch((err) => { console.error('Initial load error:', err); });
  }, [fetchHardwareMetrics]);

  useEffect(() => {
    fetchModelStatus().catch((err) => { console.error('Initial model status error:', err); });
  }, [fetchModelStatus]);

  return (
    <div className="min-h-screen bg-bg mx-auto">
      <div className={`sticky top-0 ${powerOffShown ? 'z-[95]' : 'z-40'} px-4 md:px-6 py-3 md:py-4 bg-bg/80 backdrop-blur border-b border-border`}>
        <div className="flex flex-wrap items-center justify-between gap-y-2">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold">vLLM-Dashboard</h1>
            {serverVersion && (
              <span className="px-1.5 py-0.5 rounded bg-bg-hover text-text-muted text-xs font-mono">v{serverVersion}</span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <SoftwareVersionBadges sshConnected={sshConnected} />
            <div className="flex items-center gap-3">
              {bmcStatus.configured && (
                <span role="status" className="flex items-center gap-1.5">
                  <span className={`w-2 h-2 rounded-full ${bmcStatus.connected ? 'bg-success animate-pulse' : 'bg-danger'}`} />
                  <span className="text-xs text-text-muted">BMC: {bmcStatus.connected ? t('Connected') : t('Disconnected')}</span>
                </span>
              )}
              <span role="status" className="flex items-center gap-1.5">
                <span className={`w-2 h-2 rounded-full ${sshConnected ? 'bg-success animate-pulse' : 'bg-danger'}`} />
                <span className="text-xs text-text-muted">SSH: {sshConnected ? t('Connected') : t('Disconnected')}</span>
              </span>
              <span role="status" className="flex items-center gap-1.5">
                <span className={`w-2 h-2 rounded-full ${apiConnected ? 'bg-success animate-pulse' : 'bg-danger'}`} />
                <span className="text-xs text-text-muted">API: {apiConnected ? t('Connected') : t('Disconnected')}</span>
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={toggleLang}
                className="px-2 py-1.5 rounded-lg hover:bg-bg-hover transition-colors text-sm font-medium text-text-muted"
                title={lang === 'en' ? '切换到中文' : 'Switch to English'}
              >
                {lang === 'en' ? '中' : 'EN'}
              </button>
              <IconButton
                ariaLabel={theme === 'dark' ? t('Switch to light theme') : t('Switch to dark theme')}
                onClick={toggleTheme}
              >
                {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
              </IconButton>
            </div>
            <span className="w-px h-5 bg-border" aria-hidden="true" />
            <div className="flex items-center gap-2">
              <IconButton
                ariaLabel={t('Server Config')}
                onClick={() => setShowServerConfig(true)}
              >
                <Settings className="w-5 h-5" />
              </IconButton>
              {bmcStatus.configured && (
                <IconButton
                  ariaLabel={serverOn ? t('Power OFF') : t('Power ON')}
                  onClick={() => (serverOn ? requestShutdownServer() : handlePowerOnClick())}
                  disabled={shuttingDown || powerOning || (!serverOn && !bmcStatus.connected)}
                  className={serverOn ? 'text-danger' : 'text-success'}
                >
                  {shuttingDown || powerOning ? <Loader2 className="w-5 h-5 animate-spin" /> : <Power className="w-5 h-5" />}
                </IconButton>
              )}
            </div>
          </div>
        </div>
      </div>

      <TopStack>
        {downloading && (
          <div className="top-stack-item pointer-events-auto flex items-center gap-3 bg-bg-card rounded-lg px-4 py-2 border border-accent/30 shadow-lg">
            <Loader2 className="w-4 h-4 animate-spin text-accent" />
            <span className="text-sm text-text">{t('Downloading {name}...', { name: activeDownloadName })}</span>
            <span className="text-sm font-mono text-accent ml-auto">{downloadProgress}%</span>
          </div>
        )}
        {downloadNotice && !downloading && (
          <div className="top-stack-item pointer-events-auto flex items-center gap-2 bg-bg-card rounded-lg px-4 py-2 border border-danger/30 shadow-lg text-xs text-danger max-w-[80vw]">
            <span className="truncate">{downloadNotice}</span>
            <button
              onClick={clearDownloadNotice}
              aria-label={t('Close')}
              className="p-1 rounded hover:bg-bg-hover transition-colors text-text-muted"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </TopStack>

      {powerOffShown && <ServerOffOverlay starting={powerOnPending} bmcConfigured={bmcStatus.configured} />}

      <div className="mt-6 px-4 md:px-6 flex flex-col lg:flex-row gap-6">
        <div className="flex-1 min-w-0 space-y-6">
          <HardwareCards sshConnected={sshConnected} />
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
            <ModelStatusCard />
            <ModelList
              models={models}
              loading={loading}
              refreshing={refreshing}
              scanMessage={scanMessage}
              error={modelError}
              starting={starting}
              sshConnected={sshConnected}
              benchmarkResults={benchmarkResults}
              deletingId={deletingId}
              stoppingId={stoppingId}
              onRefresh={handleRefresh}
              onStart={handleStartWithAutoBench}
              onStop={handleStopModel}
              onOpenLaunchConfig={handleOpenLaunchConfig}
              onDelete={handleDeleteModel}
              onOpenDownload={openDownloadModal}
              onOpenBenchmark={openBenchmarkModal}
            />
          </div>
        </div>

        <div className="lg:w-1/2 flex-shrink-0">
          <ConsolePanel onSendInput={sendConsoleInput} onSendResize={sendConsoleResize} />
        </div>
      </div>

      {showServerConfig && (
        <ServerConfigModal
          config={serverConfig}
          onConfigChange={setServerConfig}
          onSave={handleSaveConfig}
          saving={savingConfig}
          message={configMessage}
          onClose={() => setShowServerConfig(false)}
        />
      )}

      {showDownloadModal && (
        <DownloadModal
          modelName={downloadModelName}
          onModelNameChange={setDownloadModelName}
          savePath={downloadModelSavePath}
          onSavePathChange={setDownloadModelSavePath}
          hfMirror={hfMirror}
          onHfMirrorChange={setHfMirror}
          checkingCli={checkingCli}
          cliStatus={cliStatus}
          installingCli={installingCli}
          installMessage={installMessage}
          onInstallCli={handleInstallCli}
          onDownload={handleDownloadModel}
          onClose={() => setShowDownloadModal(false)}
        />
      )}

      {showLaunchConfigModal && (
        <LaunchConfigModal
          modelName={launchConfigModelName}
          modelPath={launchConfigModelPath}
          command={launchCommand}
          onCommandChange={setLaunchCommand}
          envVars={launchEnvVars}
          onEnvVarsChange={setLaunchEnvVars}
          onSave={handleSaveLaunchConfig}
          saving={savingLaunchConfig}
          onClose={() => setShowLaunchConfigModal(false)}
        />
      )}

      {showBenchmarkModal && (
        <BenchmarkModal
          models={models}
          benchmarkResults={benchmarkResults}
          benchmarkingIds={benchmarkingIds}
          benchmarkErrors={benchmarkErrors}
          onBenchmark={handleBenchmark}
          onClose={() => setShowBenchmarkModal(false)}
        />
      )}

      {deleteRequest && (
        <ConfirmDialog
          title={t('Delete {name}?', { name: deleteRequest.name })}
          description={t('The model files on the server will also be deleted.')}
          confirmLabel={t('Delete')}
          danger
          onConfirm={confirmDelete}
          onCancel={cancelDelete}
        />
      )}

      {stopRequest && (
        <ConfirmDialog
          title={t('Stop {name}?', { name: stopRequest.name })}
          description={t('Ongoing inference requests will be interrupted.')}
          confirmLabel={t('Stop')}
          onConfirm={confirmStop}
          onCancel={cancelStop}
        />
      )}

      {shutdownRequested && (
        <ConfirmDialog
          title={t('Shut down the AI server?')}
          description={t('The machine will power off and vLLM will be stopped. You will need to power it back on to use it again.')}
          confirmLabel={t('Power OFF')}
          danger
          onConfirm={confirmShutdownServer}
          onCancel={cancelShutdownServer}
        />
      )}
    </div>
  );
}
