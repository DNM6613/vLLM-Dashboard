import { useCallback, useEffect, useRef, useState } from 'react';
import { errMsg, useModelStore } from '../stores/model';
import { getServerConfig, getSshStatus, getServerHealth, getDashboardHealth, updateServerConfig, shutdownServer, powerOnServer, getBmcStatus, resetModelsAfterPowerOff } from '../api/serverConfig';
import type { ServerConfig as ServerConfigType, BmcStatus, ServerHealth } from '../types';
import { useI18n } from '../i18n/context';
import type { LightState } from '../utils/lightStatus';

// v6: no frontend debounce for the header lights — the backend owns it
// (per-request fresh probes + the ICMP 3-strike tracker). These callbacks
// render whatever the probe response says; a failed dashboard-API request
// keeps the last rendered state instead of flapping the lights.

const LIGHT_DOWN: LightState = { net: 'down', code: null, connected: false };

export function useServerConfig() {
  const { t } = useI18n();
  const [serverConfig, setServerConfig] = useState<ServerConfigType>({
    id: 'default', host: '', use_auth: false, ssh_port: 22,
    ssh_username: '', ssh_password: '', ssh_key_path: '', venv_name: '.vllm',
    model_save_path: '',
    bmc_host: '', bmc_username: '', bmc_password: '', hf_token: '',
  });
  const [savingConfig, setSavingConfig] = useState(false);
  const [configMessage, setConfigMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [showServerConfig, setShowServerConfig] = useState(false);
  const [sshLight, setSshLight] = useState<LightState>(LIGHT_DOWN);
  const [apiLight, setApiLight] = useState<LightState>(LIGHT_DOWN);
  const [bmcStatus, setBmcStatus] = useState<BmcStatus>({ configured: false, connected: false, power: null, powerFresh: false });
  const bmcPrevPowerRef = useRef<BmcStatus['power']>(null);
  const [bmcProbed, setBmcProbed] = useState(false);
  const [serverVersion, setServerVersion] = useState<string | null>(null);
  const [shuttingDown, setShuttingDown] = useState(false);
  const shuttingDownRef = useRef(false);
  const [shutdownRequested, setShutdownRequested] = useState(false);
  const [powerOning, setPowerOning] = useState(false);
  const powerOningRef = useRef(false);

  const [configLoadFailed, setConfigLoadFailed] = useState(false);
  // Audit 2026-10-22 FE-11: stable identity so the retry effect below can
  // depend on it without rebuilding the probe intervals.
  const loadServerConfig = useCallback(async () => {
    try {
      const configData = await getServerConfig();
      setServerConfig(configData);
      setConfigLoadFailed(false);
    } catch (error: unknown) {
      console.error('Failed to load config:', error);
      setConfigLoadFailed(true);
    }
  }, []);

  const checkSshConnection = useCallback(async () => {
    let status;
    try {
      status = await getSshStatus();
    } catch {
      return; // dashboard API unreachable — keep the last rendered light
    }
    setSshLight({
      net: status.net_state ?? (status.host_down ? 'down' : 'degraded'),
      code: status.error_code ?? (status.connected ? null : 'no_response'),
      connected: !!status.connected,
      probed: true,
    });
  }, []);

  const checkApiConnection = useCallback(async () => {
    try {
      const health: ServerHealth = await getServerHealth();
      setApiLight({
        net: health.reachable === false ? 'down' : 'up',
        code: health.error_code ?? (health.service_ok ? null : 'no_response'),
        connected: health.service_ok === true,
        probed: true,
      });
    } catch {
      // The dashboard's own API is unreachable — keep the last rendered light.
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
    const prevPower = bmcPrevPowerRef.current;
    // Audit 2026-10-23 D-7: only a REAL reading (non-null) may move the
    // edge detector — a null from a failed probe would wipe the last known
    // state, so the first reading back after a BMC blip (e.g. 'off' on an
    // already-off machine) re-triggered resetModelsAfterPowerOff.
    if (status.power !== null) {
      bmcPrevPowerRef.current = status.power;
    }
    if (status.power === 'off' && prevPower !== 'off') {
      resetModelsAfterPowerOff().catch(() => {  });
    }
    const rawConnected = status.connected;
    setBmcStatus((prev) => {
      // v6: the backend's ping 3-strike tracker owns the light color; the
      // frontend only holds the last known power reading while the probe is
      // down (`power` is sticky, `powerFresh` marks a fresh reading).
      const power = rawConnected ? status.power : prev.power;
      const configured = probeFailed ? prev.configured : status.configured;
      const next: BmcStatus = {
        ...status,
        configured,
        connected: rawConnected,
        power,
        powerFresh: rawConnected,
        net_state: status.net_state ?? (rawConnected ? 'up' : 'degraded'),
        error_code: status.error_code ?? (rawConnected ? null : 'no_response'),
      };
      return prev.configured === next.configured && prev.connected === next.connected &&
        prev.power === next.power && prev.powerFresh === next.powerFresh &&
        prev.error === next.error && prev.net_state === next.net_state
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
  }, [loadServerConfig]);

  // Audit 2026-10-22 FE-11: the initial load ran once on mount; if it failed
  // (a transient blip, or the API key not (re)entered yet), saving would stay
  // disabled until a full page reload. Retry once the API answers again.
  const apiConnected = apiLight.connected;
  useEffect(() => {
    if (apiConnected && configLoadFailed) {
      loadServerConfig();
    }
  }, [apiConnected, configLoadFailed, loadServerConfig]);

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

  const bmcConfigured = bmcStatus.configured || (serverConfig.bmc_host !== '' && !bmcProbed);
  const sshConnected = sshLight.connected;

  return {
    serverConfig,
    setServerConfig,
    savingConfig,
    configMessage,
    showServerConfig,
    setShowServerConfig,
    // Header lights: network-layer state (color) + specific error code
    // (text). v6 — no frontend debounce, the backend probes fresh per
    // request and owns the 3-strike ping debounce.
    sshLight,
    apiLight,
    // Service-layer connectivity (model ops, hardware cards, badges):
    // can we actually run SSH commands / reach vLLM right now.
    sshConnected,
    apiConnected,
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
