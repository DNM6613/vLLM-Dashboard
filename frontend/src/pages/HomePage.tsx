import { useCallback, useEffect, useRef, useState } from 'react';
import { Settings, Power, Loader2, Sun, Moon } from 'lucide-react';
import { useI18n } from '../i18n';
import { useTheme } from '../hooks/useTheme';
import { invalidateHardwareFetch, invalidateSoftwareFetch, useHardwareStore } from '../stores/hardware';
import { useModelStore, errMsgLocalized } from '../stores/model';
import { useServerConfig } from '../hooks/useServerConfig';
import { useTasks } from '../hooks/useTasks';
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
import { DeployEnvModal } from '../components/modals/DeployEnvModal';
import { DownloadModal } from '../components/modals/DownloadModal';
import { LaunchConfigModal } from '../components/modals/LaunchConfigModal';
import { BenchmarkModal } from '../components/modals/BenchmarkModal';
import { TaskCenterDrawer } from '../components/TaskCenterDrawer';
import { terminalReset } from '../utils/terminalSink';
import { useSettledFlag } from '../hooks/useSettledFlag';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { IconButton } from '../components/ui/IconButton';
import { showToast } from '../components/ui/toast';

// After clicking power-on the button keeps spinning for this long (unless
// the server comes up sooner): the backend reports success as soon as the
// machine answers on the network, which is well before the OS is usable, so
// a fixed wait window tells the user to keep waiting.
const POWER_ON_WAIT_MS = 99 * 1000;

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
  const { showDownloadModal, setShowDownloadModal, downloadModelName, setDownloadModelName, downloadModelSavePath, setDownloadModelSavePath, hfMirror, setHfMirror, downloading, downloadProgress, downloadSizeBytes, downloadTotalSizeBytes, downloadStalledSecs, activeDownloadName, cliStatus, checkingCli, installingCli, installMessage, handleInstallCli, handleDownloadModel, handleCancelDownload } = useModelDownload({ defaultSavePath: serverConfig.model_save_path ?? '', onSavePathPersisted: (path: string) => setServerConfig((c) => ({ ...c, model_save_path: path })) });

  const [showDeployEnv, setShowDeployEnv] = useState(false);
  const tasks = useTasks();

  const downloadProgressInfo = downloading
    ? {
        name: activeDownloadName,
        progress: downloadProgress,
        sizeBytes: downloadSizeBytes,
        totalSizeBytes: downloadTotalSizeBytes,
        stalledSecs: downloadStalledSecs,
      }
    : null;

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

  const [powerOnWaiting, setPowerOnWaiting] = useState(false);
  const powerOnWaitRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearPowerOnWait = useCallback(() => {
    if (powerOnWaitRef.current) {
      clearTimeout(powerOnWaitRef.current);
      powerOnWaitRef.current = null;
    }
  }, []);
  const handlePowerOnClick = useCallback(async () => {
    setPowerOnWaiting(true);
    clearPowerOnWait();
    powerOnWaitRef.current = setTimeout(() => {
      powerOnWaitRef.current = null;
      setPowerOnWaiting(false);
    }, POWER_ON_WAIT_MS);
    try {
      await handlePowerOnServer();
    } catch (e: unknown) {
      clearPowerOnWait();
      setPowerOnWaiting(false);
      const reason = errMsgLocalized(e);
      showToast(`${t('Power-on failed')}${reason ? `: ${reason}` : ''}`, 'error');
    }
  }, [handlePowerOnServer, clearPowerOnWait, t]);
  useEffect(() => {
    if (serverOn) {
      clearPowerOnWait();
      setPowerOnWaiting(false);
    }
  }, [serverOn, clearPowerOnWait]);
  useEffect(() => clearPowerOnWait, [clearPowerOnWait]);

  useEffect(() => {
    fetchHardwareMetrics().catch((err) => { console.error('Initial load error:', err); });
  }, [fetchHardwareMetrics]);

  useEffect(() => {
    fetchModelStatus().catch((err) => { console.error('Initial model status error:', err); });
  }, [fetchModelStatus]);

  return (
    <div className="min-h-screen bg-bg mx-auto">
      <div className="sticky top-0 z-40 px-4 md:px-6 py-3 md:py-4 bg-bg/80 backdrop-blur border-b border-border">
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
                  disabled={shuttingDown || powerOning || powerOnWaiting || (!serverOn && !bmcStatus.connected)}
                  className={serverOn ? 'text-danger' : 'text-success'}
                >
                  {shuttingDown || powerOning || powerOnWaiting ? <Loader2 className="w-5 h-5 animate-spin" /> : <Power className="w-5 h-5" />}
                </IconButton>
              )}
            </div>
          </div>
        </div>
      </div>

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
              download={downloadProgressInfo}
              onCancelDownload={handleCancelDownload}
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
          onOpenDeployEnv={() => setShowDeployEnv(true)}
        />
      )}

      {showDeployEnv && <DeployEnvModal onClose={() => setShowDeployEnv(false)} />}

      <TaskCenterDrawer tasks={tasks} />

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
