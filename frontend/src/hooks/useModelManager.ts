import { useCallback, useEffect, useRef, useState } from 'react';
import { errMsg, errMsgLocalized, invalidateModelFetch, useModelStore } from '../stores/model';
import { useConsoleStore } from '../stores/console';
import { ModelStatus } from '../types';
import { useI18n } from '../i18n';
import { showToast } from '../components/ui/toast';

interface UseModelManagerParams {
  startConsoleForModel: (command: string) => void;
  sshConnected: boolean;
  connectConsole: () => void;
}

export function useModelManager({ startConsoleForModel, sshConnected, connectConsole }: UseModelManagerParams) {
  const { t } = useI18n();
  const { fetchModels, syncModelStatus, stopModel, startModel, clearError, scanModels, getLaunchConfig, saveLaunchConfig, deleteModel } = useModelStore();
  const [refreshing, setRefreshing] = useState(false);
  const [scanMessage, setScanMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [showLaunchConfigModal, setShowLaunchConfigModal] = useState(false);
  const [launchConfigModelId, setLaunchConfigModelId] = useState('');
  const [launchConfigModelName, setLaunchConfigModelName] = useState('');
  const [launchConfigModelPath, setLaunchConfigModelPath] = useState('');
  const [launchCommand, setLaunchCommand] = useState('');
  const [launchEnvVars, setLaunchEnvVars] = useState('');
  const [savingLaunchConfig, setSavingLaunchConfig] = useState(false);
  const startingRef = useRef(false);
  const [starting, setStarting] = useState(false);
  const deletingRef = useRef(false);
  const stoppingRef = useRef(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [stoppingId, setStoppingId] = useState<string | null>(null);
  const [deleteRequest, setDeleteRequest] = useState<{ modelId: string; name: string } | null>(null);
  const [stopRequest, setStopRequest] = useState<{ modelId: string; name: string } | null>(null);

  useEffect(() => {
    if (!sshConnected) return;
    const interval = setInterval(() => {
      fetchModels().catch(() => {});
    }, 15000);
    return () => clearInterval(interval);
  }, [sshConnected, fetchModels]);

  useEffect(() => {
    if (!sshConnected) {
      invalidateModelFetch();
      useModelStore.setState({ models: [], loading: false, error: null });
    }
  }, [sshConnected]);

  const chainInFlightRef = useRef(false);
  const runPostConnectChain = useCallback(async () => {
    if (chainInFlightRef.current) {
      if (!useConsoleStore.getState().connected) {
        connectConsole();
      }
      return;
    }
    chainInFlightRef.current = true;
    try {
      useModelStore.setState({ loading: true });
      if (!useConsoleStore.getState().connected) {
        connectConsole();
      }
      let scanResult = await scanModels();
      let retries = 0;
      while (scanResult?.error && retries < 3) {
        await new Promise((r) => setTimeout(r, 10000));
        scanResult = await scanModels();
        retries++;
      }
      await syncModelStatus();
      await fetchModels();
      const current = useModelStore.getState().models;
      if (current.some(m => m.status === ModelStatus.LOADING || m.status === ModelStatus.RUNNING) &&
          !useConsoleStore.getState().connected) {
        connectConsole();
      }
    } finally {
      chainInFlightRef.current = false;
    }
  }, [scanModels, syncModelStatus, fetchModels, connectConsole]);

  const prevSshConnectedRef = useRef(sshConnected);
  useEffect(() => {
    const prev = prevSshConnectedRef.current;
    prevSshConnectedRef.current = sshConnected;
    if (sshConnected && !prev) {
      runPostConnectChain().catch(() => {  });
    }
  }, [sshConnected, runPostConnectChain]);

  useEffect(() => { return () => { clearError(); }; }, [clearError]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setScanMessage(null);
    try {
      const scanResult = await scanModels();
      const syncResult = await syncModelStatus();
      await fetchModels();
      const parts: string[] = [];
      let hasError = false;
      if (scanResult && typeof scanResult === 'object' && 'count' in scanResult) {
        if (scanResult.error) { hasError = true; parts.push(t('Scan failed: {error}', { error: scanResult.error })); }
        else {
          parts.push(t('Scanned {count} models', { count: scanResult.count }));
          if (scanResult.removed_stale?.length) parts.push(t('Removed {count} stale model(s)', { count: scanResult.removed_stale.length }));
        }
      }
      if (syncResult && typeof syncResult === 'object' && 'synced' in syncResult) {
        if (syncResult.synced) parts.push(t('Updated {count} statuses', { count: syncResult.synced_count || 0 }));
        else { hasError = true; parts.push(syncResult.error || t('Sync failed')); }
      }
      setScanMessage({ type: hasError ? 'error' : 'success', text: parts.join(' ') || t('Refreshed') });
      setTimeout(() => setScanMessage(null), hasError ? 5000 : 3000);
    } catch (e: unknown) {
      setScanMessage({ type: 'error', text: errMsgLocalized(e) || t('Refresh failed') });
      setTimeout(() => setScanMessage(null), 5000);
    } finally { setRefreshing(false); }
  }, [scanModels, syncModelStatus, fetchModels, t]);

  const handleOpenLaunchConfig = useCallback(async (modelId: string, modelName: string, modelPath: string) => {
    setLaunchConfigModelId(modelId);
    setLaunchConfigModelName(modelName);
    setLaunchConfigModelPath(modelPath);
    try {
      const result = await getLaunchConfig(modelId);
      setLaunchCommand(result.config?.start_command || '');
      setLaunchEnvVars(result.config?.env_vars || '');
      setShowLaunchConfigModal(true);
    } catch {
    }
  }, [getLaunchConfig]);

  const handleSaveLaunchConfig = async () => {
    setSavingLaunchConfig(true);
    try {
      await saveLaunchConfig(launchConfigModelId, { start_command: launchCommand, env_vars: launchEnvVars });
      setShowLaunchConfigModal(false);
    } catch (e: unknown) {
      showToast(errMsgLocalized(e) || t('Operation failed'));
    } finally { setSavingLaunchConfig(false); }
  };

  const handleDeleteModel = useCallback((modelId: string, modelName: string) => {
    if (deletingRef.current) return;
    setDeleteRequest({ modelId, name: modelName });
  }, []);

  const confirmDelete = useCallback(async () => {
    const req = deleteRequest;
    if (!req) return;
    setDeleteRequest(null);
    deletingRef.current = true;
    setDeletingId(req.modelId);
    try {
      await deleteModel(req.modelId);
      await fetchModels();
    } catch (e: unknown) {
      showToast(errMsgLocalized(e) || t('Operation failed'));
    } finally {
      deletingRef.current = false;
      setDeletingId(null);
    }
  }, [deleteRequest, deleteModel, fetchModels, t]);

  const cancelDelete = useCallback(() => setDeleteRequest(null), []);

  const handleStartModel = useCallback(async (modelId: string): Promise<boolean> => {
    if (startingRef.current) return false;
    startingRef.current = true;
    setStarting(true);
    try {
      const startResult = await startModel(modelId);
      const command = startResult.command;
      startConsoleForModel(command);
      await fetchModels();
      return true;
    } catch (e: unknown) {
      const msg = errMsg(e);
      if (msg.includes('Start command not configured')) {
        showToast(t("Start command not configured — open the model's Launch Config and set a start command first."));
      } else {
        showToast(t('Start failed: {msg}', { msg: msg || t('unknown error') }));
      }
      return false;
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  }, [startModel, fetchModels, startConsoleForModel, t]);

  const doStopModel = useCallback(async (modelId: string) => {
    stoppingRef.current = true;
    setStoppingId(modelId);
    useModelStore.setState(state => ({
      models: state.models.map(m => m.id === modelId ? { ...m, status: ModelStatus.STOPPED } : m)
    }));
    try {
      const result = await stopModel(modelId);
      if (result && result.stopped === false) {
        showToast(t('Stop timed out: the process may still be running. GPU memory may remain in use.'));
      }
    } catch (e: unknown) {
      showToast(errMsgLocalized(e) || t('Operation failed'));
    } finally {
      stoppingRef.current = false;
      setStoppingId(null);
      fetchModels();
    }
  }, [stopModel, fetchModels, t]);

  const handleStopModel = useCallback((modelId: string) => {
    if (stoppingRef.current) return;
    const model = useModelStore.getState().models.find(m => m.id === modelId);
    if (model?.status === ModelStatus.RUNNING) {
      setStopRequest({ modelId, name: model.name });
      return;
    }
    void doStopModel(modelId);
  }, [doStopModel]);

  const confirmStop = useCallback(async () => {
    const req = stopRequest;
    if (!req) return;
    setStopRequest(null);
    await doStopModel(req.modelId);
  }, [stopRequest, doStopModel]);

  const cancelStop = useCallback(() => setStopRequest(null), []);

  return {
    refreshing,
    scanMessage,
    starting,
    showLaunchConfigModal,
    setShowLaunchConfigModal,
    launchConfigModelName,
    launchConfigModelPath,
    launchCommand,
    setLaunchCommand,
    launchEnvVars,
    setLaunchEnvVars,
    savingLaunchConfig,
    handleRefresh,
    handleStartModel,
    handleStopModel,
    handleOpenLaunchConfig,
    handleSaveLaunchConfig,
    handleDeleteModel,
    deletingId,
    stoppingId,
    deleteRequest,
    confirmDelete,
    cancelDelete,
    stopRequest,
    confirmStop,
    cancelStop,
  };
}
