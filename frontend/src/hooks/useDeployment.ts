import { useCallback, useEffect, useRef, useState } from 'react';
import { showToast } from '../components/ui/toast';
import { tRaw } from '../i18n/strings';
import {
  apiErrorMessage,
  applyCuda,
  applyDriver,
  applyVllm,
  exportEnv,
  getCudaInfo,
  getDeployState,
  getDrivers,
  getPreflight,
  getTemplates,
  getVllmInfo,
  importEnv,
  rebootServer,
  rollbackEnv,
  scanConflicts,
  updateDeployState,
  type CudaInfo,
  type ConflictPackage,
  type DeployState,
  type DeployTemplate,
  type DriverList,
  type Preflight,
  type VllmApplyPayload,
  type VllmInfo,
} from '../api/deployment';

export function useDeployment(open: boolean) {
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [drivers, setDrivers] = useState<DriverList | null>(null);
  const [cuda, setCuda] = useState<CudaInfo | null>(null);
  const [vllm, setVllm] = useState<VllmInfo | null>(null);
  const [state, setState] = useState<DeployState | null>(null);
  const [templates, setTemplates] = useState<DeployTemplate[]>([]);
  const [conflicts, setConflicts] = useState<ConflictPackage[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  // Each probe settles independently: the AI server's sshd intermittently
  // stalls for tens of seconds, and one slow probe must not blank the whole
  // modal — the old Promise.all rendered "nothing at all" whenever any
  // single endpoint timed out. Successful probes render immediately; the
  // failures become a partial-load notice with a retry via Re-check.
  const loadAll = useCallback(async () => {
    setPreflightLoading(true);
    const failures: string[] = [];
    const track = async <T>(set: (v: T) => void, fn: () => Promise<T>, key: string) => {
      try {
        set(await fn());
      } catch {
        failures.push(key);
      }
    };
    await Promise.all([
      track(setPreflight, getPreflight, 'preflight'),
      track(setDrivers, getDrivers, 'drivers'),
      track(setState, getDeployState, 'state'),
      track(setCuda, getCudaInfo, 'cuda'),
      track(setVllm, getVllmInfo, 'vllm'),
    ]);
    setPreflightLoading(false);
    if (failures.length === 0) {
      setError('');
    } else {
      setError(
        tRaw('Partial load failed ({n} of 5) — retry with Re-check.')
          .replace('{n}', String(failures.length)),
      );
    }
  }, []);

  useEffect(() => {
    if (open) {
      loadAll();
      getTemplates().then(setTemplates).catch(() => undefined);
    }
  }, [open, loadAll]);

  const refreshDrivers = useCallback(async () => {
    try {
      setDrivers(await getDrivers());
    } catch {
      // keep the previous snapshot
    }
  }, []);

  const refreshState = useCallback(async () => {
    try {
      setState(await getDeployState());
    } catch {
      // keep the previous snapshot
    }
  }, []);

  const refreshCuda = useCallback(async () => {
    try {
      setCuda(await getCudaInfo());
    } catch {
      // keep the previous snapshot
    }
  }, []);

  const refreshVllm = useCallback(async () => {
    try {
      setVllm(await getVllmInfo());
    } catch {
      // keep the previous snapshot
    }
  }, []);

  const refreshPreflight = useCallback(async () => {
    try {
      setPreflight(await getPreflight());
    } catch {
      // keep the previous snapshot
    }
  }, []);

  // In-flight guard: a stalled probe (the AI server's sshd occasionally
  // holds a channel open for tens of seconds) must not pile up behind every
  // 5s tick — overlapping probes on one pooled connection were amplifying
  // the AI server's load exactly when it was slow.
  const pollInFlight = useRef(false);

  // Light polling while the modal is open: driver reconciliation (post-reboot
  // verification) + task-driven state changes surface without manual refresh.
  useEffect(() => {
    if (!open) return;
    const timer = setInterval(() => {
      if (pollInFlight.current) return;
      pollInFlight.current = true;
      Promise.all([
        refreshPreflight(),
        refreshDrivers(),
        refreshState(),
        refreshCuda(),
        refreshVllm(),
      ]).finally(() => {
        pollInFlight.current = false;
      });
    }, 5000);
    return () => clearInterval(timer);
  }, [open, refreshPreflight, refreshDrivers, refreshState, refreshCuda, refreshVllm]);

  const runAction = useCallback(async (fn: () => Promise<unknown>, successMsg = ''): Promise<boolean> => {
    setBusy(true);
    setError('');
    try {
      await fn();
      if (successMsg) showToast(tRaw(successMsg), 'info');
      await refreshDrivers();
      await refreshState();
      return true;
    } catch (e) {
      setError(apiErrorMessage(e));
      return false;
    } finally {
      setBusy(false);
    }
  }, [refreshDrivers, refreshState]);

  const handleApplyDriver = useCallback((pkg: string) => runAction(
    () => applyDriver(pkg),
    'Driver task started — follow it in the Task Center.',
  ), [runAction]);

  const handleReboot = useCallback(() => runAction(() => rebootServer()), [runAction]);

  const handleCuda = useCallback((version: string, installSystem: boolean) =>
    runAction(async () => {
      const result = await applyCuda(version, installSystem);
      if (result.task_id) setCuda(await getCudaInfo());
    }), [runAction]);

  const handleVllm = useCallback((payload: VllmApplyPayload) => runAction(
    () => applyVllm(payload),
    'vLLM install task started — follow it in the Task Center.',
  ), [runAction]);

  const handleRollback = useCallback((target: 'driver' | 'vllm') => runAction(
    () => rollbackEnv(target),
  ), [runAction]);

  const handleMirrors = useCallback(async (mirrors: { pypi?: string; hf?: string }) => {
    try {
      setState(await updateDeployState({ mirrors }));
      setError('');
      return true;
    } catch (e) {
      setError(apiErrorMessage(e));
      return false;
    }
  }, []);

  const handleTemplate = useCallback((tpl: DeployTemplate) => {
    // Prefill the three tabs from a template; the user still confirms each step.
    updateDeployState({
      selected: {
        driver: tpl.driver,
        cuda: tpl.cuda,
        cuda_install_system: tpl.cuda_install_system,
        vllm_version: tpl.vllm_version,
        vllm_runtime: tpl.vllm_runtime,
      },
    }).then(setState).catch((e) => setError(apiErrorMessage(e)));
  }, []);

  const handleScanConflicts = useCallback(async () => {
    try {
      setConflicts(await scanConflicts());
      setError('');
    } catch (e) {
      setError(apiErrorMessage(e));
    }
  }, []);

  const handleExport = useCallback(async (): Promise<{ yaml: string; filename: string } | null> => {
    try {
      return await exportEnv();
    } catch (e) {
      setError(apiErrorMessage(e));
      return null;
    }
  }, []);

  const handleImport = useCallback(async (yaml: string): Promise<boolean> => {
    try {
      setState(await importEnv(yaml));
      setError('');
      return true;
    } catch (e) {
      setError(apiErrorMessage(e));
      return false;
    }
  }, []);

  return {
    preflight,
    preflightLoading,
    drivers,
    cuda,
    vllm,
    state,
    templates,
    conflicts,
    error,
    busy,
    loadAll,
    refreshDrivers,
    refreshCuda,
    refreshVllm,
    handleApplyDriver,
    handleReboot,
    handleCuda,
    handleVllm,
    handleRollback,
    handleMirrors,
    handleTemplate,
    handleScanConflicts,
    handleExport,
    handleImport,
  };
}

export type UseDeployment = ReturnType<typeof useDeployment>;
