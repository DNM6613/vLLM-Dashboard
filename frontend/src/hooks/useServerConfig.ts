import { useCallback, useEffect, useRef, useState } from 'react';
import { errMsg, useModelStore } from '../stores/model';
import { showToast } from '../components/ui/toast';
import { getServerConfig, getSshStatus, getServerHealth, getDashboardHealth, updateServerConfig, shutdownServer, powerOnServer, getBmcStatus, resetModelsAfterPowerOff } from '../api/serverConfig';
import type { ServerConfig as ServerConfigType, BmcStatus } from '../types';
import { useI18n } from '../i18n';

const SSH_FAIL_THRESHOLD = 3;

const BMC_DISCONNECT_HOLD_MS = 30_000;

export function useServerConfig() {
  const { t } = useI18n();
  const [serverConfig, setServerConfig] = useState<ServerConfigType>({
    id: 'default', host: '', port: 8000, use_auth: false, ssh_port: 22,
    ssh_username: '', ssh_password: '', ssh_key_path: '', venv_name: '.vllm',
    model_save_path: '',
    bmc_host: '', bmc_username: '', bmc_password: '',
  });
  const [savingConfig, setSavingConfig] = useState(false);
  const [configMessage, setConfigMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [showServerConfig, setShowServerConfig] = useState(false);
  const [sshConnected, setSshConnected] = useState(false);
  const sshFailStreakRef = useRef(0);
  const [apiConnected, setApiConnected] = useState(false);
  const [bmcStatus, setBmcStatus] = useState<BmcStatus>({ configured: false, connected: false, power: null });
  const bmcFirstFailAtRef = useRef<number | null>(null);
  const bmcPrevPowerRef = useRef<BmcStatus['power']>(null);
  const [bmcProbed, setBmcProbed] = useState(false);
  const [serverVersion, setServerVersion] = useState<string | null>(null);
  const [shuttingDown, setShuttingDown] = useState(false);
  const shuttingDownRef = useRef(false);
  const [shutdownRequested, setShutdownRequested] = useState(false);
  const [powerOning, setPowerOning] = useState(false);
  const powerOningRef = useRef(false);

  const [configLoadFailed, setConfigLoadFailed] = useState(false);
  const loadServerConfig = async () => {
    try {
      const configData = await getServerConfig();
      setServerConfig(configData);
      setConfigLoadFailed(false);
    } catch (error: unknown) {
      console.error('Failed to load config:', error);
      setConfigLoadFailed(true);
    }
  };

  const checkSshConnection = useCallback(async () => {
    let failed = false;
    let hostDown = false;
    try {
      const status = await getSshStatus();
      failed = !status.connected;
      hostDown = failed && !!status.host_down;
    } catch {
      failed = true;
    }
    if (!failed) {
      sshFailStreakRef.current = 0;
      setSshConnected(true);
    } else if (hostDown) {
      sshFailStreakRef.current = 0;
      setSshConnected(false);
    } else {
      sshFailStreakRef.current += 1;
      setSshConnected((prev) => prev && sshFailStreakRef.current < SSH_FAIL_THRESHOLD);
    }
  }, []);

  const checkApiConnection = useCallback(async () => {
    try {
      await getServerHealth();
      setApiConnected(true);
    } catch {
      setApiConnected(false);
    }
    try {
      const dashboardHealth = await getDashboardHealth();
      if (dashboardHealth.version) setServerVersion(dashboardHealth.version);
    } catch {
    }
  }, []);

  const checkBmcConnection = useCallback(async () => {
    let status: BmcStatus;
    let probeFailed = false;
    try {
      status = await getBmcStatus();
    } catch {
      probeFailed = true;
      status = { configured: false, connected: false, power: null, error: 'BMC probe request failed' };
    }
    if (status.connected) {
      bmcFirstFailAtRef.current = null;
    } else if (bmcFirstFailAtRef.current === null) {
      bmcFirstFailAtRef.current = Date.now();
    }
    const prevPower = bmcPrevPowerRef.current;
    bmcPrevPowerRef.current = status.power;
    if (status.power === 'off' && prevPower !== 'off') {
      resetModelsAfterPowerOff().catch(() => {  });
    }
    const firstFailAt = bmcFirstFailAtRef.current;
    const sustainedDown = firstFailAt !== null
      && Date.now() - firstFailAt >= BMC_DISCONNECT_HOLD_MS;
    const rawConnected = status.connected;
    setBmcStatus((prev) => {
      const connected = rawConnected || (prev.connected && !sustainedDown);
      const power = rawConnected ? status.power : prev.power;
      const configured = probeFailed ? prev.configured : status.configured;
      const next: BmcStatus = { ...status, connected, power, configured };
      return prev.configured === next.configured && prev.connected === next.connected &&
        prev.power === next.power && prev.error === next.error
        ? prev
        : next;
    });
    setBmcProbed(true);
  }, []);

  const checkServerConnection = useCallback(async () => {
    await Promise.all([checkSshConnection(), checkApiConnection(), checkBmcConnection()]);
  }, [checkSshConnection, checkApiConnection, checkBmcConnection]);

  const checkApiBmcConnection = useCallback(async () => {
    await Promise.all([checkApiConnection(), checkBmcConnection()]);
  }, [checkApiConnection, checkBmcConnection]);

  useEffect(() => {
    loadServerConfig();
  }, []);

  const sshProbeInFlightRef = useRef(false);
  const apiBmcProbeInFlightRef = useRef(false);

  useEffect(() => {
    const tick = () => {
      if (sshProbeInFlightRef.current) return;
      sshProbeInFlightRef.current = true;
      checkSshConnection().finally(() => { sshProbeInFlightRef.current = false; });
    };
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [checkSshConnection]);

  useEffect(() => {
    const tick = () => {
      if (apiBmcProbeInFlightRef.current) return;
      apiBmcProbeInFlightRef.current = true;
      checkApiBmcConnection().finally(() => { apiBmcProbeInFlightRef.current = false; });
    };
    tick();
    const interval = setInterval(tick, 2000);
    return () => clearInterval(interval);
  }, [checkApiBmcConnection]);

  const handleSaveConfig = async (payload?: Partial<ServerConfigType>) => {
    if (configLoadFailed) {
      setConfigMessage({ type: 'error', text: t('Config load failed — reload the page before saving') });
      return;
    }
    setSavingConfig(true);
    setConfigMessage(null);
    try {
      const newConfig = await updateServerConfig(payload ?? { ...serverConfig });
      setServerConfig(newConfig);
      showToast(t('Configuration saved'), 'info');
      setShowServerConfig(false);
      checkServerConnection();
    } catch (error: unknown) {
      setConfigMessage({ type: 'error', text: errMsg(error) || t('Save failed') });
    } finally {
      setSavingConfig(false);
    }
  };

  const requestShutdownServer = useCallback(() => {
    if (shuttingDownRef.current) return;
    setShutdownRequested(true);
  }, []);

  const confirmShutdownServer = useCallback(async () => {
    setShutdownRequested(false);
    if (shuttingDownRef.current) return;
    shuttingDownRef.current = true;
    setShuttingDown(true);
    try {
      await shutdownServer();
    } finally {
      shuttingDownRef.current = false;
      setShuttingDown(false);
      useModelStore.getState().syncModelStatus()
        .then(() => useModelStore.getState().fetchModels())
        .catch(() => {  });
    }
  }, []);

  const cancelShutdownServer = useCallback(() => {
    setShutdownRequested(false);
  }, []);

  const handlePowerOnServer = useCallback(async () => {
    if (powerOningRef.current) return;
    powerOningRef.current = true;
    setPowerOning(true);
    try {
      await powerOnServer();
    } finally {
      powerOningRef.current = false;
      setPowerOning(false);
    }
  }, []);

  const powerOff = bmcStatus.power === 'off';

  const bmcConfigured = bmcStatus.configured || (serverConfig.bmc_host !== '' && !bmcProbed);

  return {
    serverConfig,
    setServerConfig,
    savingConfig,
    configMessage,
    showServerConfig,
    setShowServerConfig,
    sshConnected: !powerOff && sshConnected,
    apiConnected: !powerOff && apiConnected,
    bmcStatus: bmcConfigured ? { ...bmcStatus, configured: true } : bmcStatus,
    serverVersion,
    shuttingDown,
    shutdownRequested,
    requestShutdownServer,
    confirmShutdownServer,
    cancelShutdownServer,
    powerOning,
    handlePowerOnServer,
    handleSaveConfig,
  };
}
