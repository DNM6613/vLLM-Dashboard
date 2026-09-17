import { useEffect, useRef, useState } from 'react';
import { errMsgLocalized, useModelStore } from '../stores/model';
import { checkCliTools, downloadModel, getDownloadStatus, getInstallStatus, installCliTool, stopDownload } from '../api/models';
import type { CliStatus } from '../types';
import { useI18n } from '../i18n';
import { showToast } from '../components/ui/toast';

interface UseModelDownloadParams {
  defaultSavePath: string;
  onSavePathPersisted: (path: string) => void;
}

export function useModelDownload({ defaultSavePath, onSavePathPersisted }: UseModelDownloadParams) {
  const { t } = useI18n();
  const tRef = useRef(t);
  tRef.current = t;
  const { fetchModels, scanModels } = useModelStore();
  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);

  const [showDownloadModal, setShowDownloadModal] = useState(false);
  const [downloadModelName, setDownloadModelName] = useState('');
  const [downloadModelSavePath, setDownloadModelSavePath] = useState('');
  const [hfMirror, setHfMirror] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState(0);
  const [activeDownloadName, setActiveDownloadName] = useState('');
  const [downloadSizeBytes, setDownloadSizeBytes] = useState(0);
  const [downloadTotalSizeBytes, setDownloadTotalSizeBytes] = useState(0);
  const [downloadStalledSecs, setDownloadStalledSecs] = useState<number | null>(null);
  const downloadRepoRef = useRef('');
  const cancelRequestedRef = useRef(false);
  const [cliStatus, setCliStatus] = useState<CliStatus | null>(null);
  const [checkingCli, setCheckingCli] = useState(false);
  const [installingCli, setInstallingCli] = useState(false);
  const [installMessage, setInstallMessage] = useState('');
  const installPollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const downloadPollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (downloadPollTimeoutRef.current) { clearTimeout(downloadPollTimeoutRef.current); downloadPollTimeoutRef.current = null; }
      if (installPollRef.current) { clearTimeout(installPollRef.current); installPollRef.current = null; }
    };
  }, []);

  useEffect(() => {
    if (showDownloadModal) {
      if (defaultSavePath) setDownloadModelSavePath((p) => (p ? p : defaultSavePath));
      setCliStatus(null);
      setCheckingCli(true);
      setInstallingCli(false);
      setInstallMessage('');
      checkCliTools()
        .then(status => { if (isMountedRef.current) { setCliStatus(status); setCheckingCli(false); } })
        .catch(() => { if (isMountedRef.current) { setCheckingCli(false); } });
    }
    return () => {
      if (installPollRef.current) { clearTimeout(installPollRef.current); installPollRef.current = null; }
    };
  }, [showDownloadModal, isMountedRef, defaultSavePath]);

  const handleInstallCli = async (tool: 'hf') => {
    setInstallingCli(true);
    setInstallMessage(t('Installing huggingface_hub...'));
    try {
      const result = await installCliTool(tool);
      const logFile = result.log_file;
      let installPollCount = 0;
      const maxInstallPolls = 1200;
      const pollInstall = async () => {
        installPollCount++;
        try {
          const status = await getInstallStatus(tool, logFile);
          if (!isMountedRef.current) return;
          if (status.status === 'installing') {
            setInstallMessage(status.message || tRef.current('Installing...'));
            if (installPollCount < maxInstallPolls) {
              installPollRef.current = setTimeout(pollInstall, 3000);
            } else {
              setInstallingCli(false);
              setInstallMessage(tRef.current('CLI install status polling stopped after 1 hour — the installation may still be running in the background. Check the CLI tools status or backend logs for its real status.'));
            }
          } else if (status.status === 'complete') {
            setInstallingCli(false);
            setInstallMessage('');
            const newStatus = await checkCliTools();
            if (isMountedRef.current) setCliStatus(newStatus);
          } else if (status.status === 'failed') {
            setInstallingCli(false);
            setInstallMessage('');
          } else {
            setInstallingCli(false);
            setInstallMessage('');
          }
        } catch {
          if (isMountedRef.current) {
            setInstallingCli(false);
            setInstallMessage('');
          }
        }
      };
      installPollRef.current = setTimeout(pollInstall, 3000);
    } catch {
      setInstallingCli(false);
      setInstallMessage('');
    }
  };

  const handleDownloadModel = async () => {
    if (downloading) return;
    if (!downloadModelName.trim()) return;
    const repoName = downloadModelName.trim();
    const savePathForDownload = downloadModelSavePath.trim();
    downloadRepoRef.current = repoName;
    cancelRequestedRef.current = false;
    setDownloading(true);
    setDownloadProgress(0);
    setDownloadSizeBytes(0);
    setDownloadTotalSizeBytes(0);
    setDownloadStalledSecs(null);
    setActiveDownloadName(repoName);
    setShowDownloadModal(false);
    try {
      const result = await downloadModel(repoName, savePathForDownload, hfMirror);
      setDownloadModelName('');
      setDownloadModelSavePath('');
      if (result.model_save_path) onSavePathPersisted(result.model_save_path);
      let pollCount = 0;
      const maxPolls = 1200;
      const logFile = result.log_file;
      const stopPolling = () => {
        showToast(tRef.current('Download status polling stopped after 1 hour — the download may still be running in the background. Check the model list or backend logs for its real status.'));
        setDownloading(false); setDownloadProgress(0); setDownloadStalledSecs(null); setActiveDownloadName(''); fetchModels();
      };
      const pollDownload = async () => {
        pollCount++;
        try {
          const status = await getDownloadStatus(logFile, repoName, savePathForDownload, hfMirror);
          if (!isMountedRef.current) return;
          setDownloadSizeBytes(status.size_bytes ?? 0);
          setDownloadTotalSizeBytes(status.total_size ?? 0);
          if (status.status === 'downloading') {
            setDownloadStalledSecs(null);
            setDownloadProgress(Math.round(status.progress ?? 0));
            if (pollCount < maxPolls) downloadPollTimeoutRef.current = setTimeout(pollDownload, 3000);
            else stopPolling();
          } else if (status.status === 'stalled') {
            setDownloadStalledSecs(status.stalled_secs ?? 0);
            setDownloadProgress(Math.round(status.progress ?? 0));
            if (pollCount < maxPolls) downloadPollTimeoutRef.current = setTimeout(pollDownload, 3000);
            else stopPolling();
          } else if (status.status === 'complete') {
            setDownloadProgress(100); setDownloadStalledSecs(null); setDownloading(false);
            setTimeout(() => { if (isMountedRef.current) { setDownloadProgress(0); setActiveDownloadName(''); } }, 2000);
            scanModels().then(() => fetchModels());
          } else if (status.status === 'failed') {
            const reason = status.reason?.slice(0, 300);
            showToast(tRef.current('Download failed') + (reason ? `\n${reason}` : ''));
            setDownloadStalledSecs(null);
            setDownloading(false); setDownloadProgress(0); setActiveDownloadName('');
            fetchModels();
          } else if (status.status === 'stopped') {
            const wasCancelled = cancelRequestedRef.current;
            cancelRequestedRef.current = false;
            setDownloadStalledSecs(null);
            if (!wasCancelled) showToast(tRef.current('Download stopped before completion'));
            setDownloading(false); setDownloadProgress(0); setActiveDownloadName('');
            fetchModels();
          } else {
            setDownloadStalledSecs(null);
            setDownloading(false); setDownloadProgress(0); setActiveDownloadName('');
            fetchModels();
          }
        } catch {
          if (!isMountedRef.current) return;
          if (pollCount < maxPolls) downloadPollTimeoutRef.current = setTimeout(pollDownload, 3000);
          else stopPolling();
        }
      };
      downloadPollTimeoutRef.current = setTimeout(pollDownload, 3000);
    } catch (e: unknown) {
      const msg = errMsgLocalized(e);
      showToast(tRef.current('Download request failed') + (msg ? `: ${msg}` : ''));
      setDownloading(false); setDownloadProgress(0); setActiveDownloadName('');
    }
  };

  const handleCancelDownload = async () => {
    const repo = downloadRepoRef.current;
    if (!repo || cancelRequestedRef.current) return;
    cancelRequestedRef.current = true;
    try {
      await stopDownload(repo);
      if (isMountedRef.current) showToast(tRef.current('Download cancelled'));
    } catch {
      cancelRequestedRef.current = false;
      if (isMountedRef.current) showToast(tRef.current('Cancel download failed'));
    }
  };

  return {
    showDownloadModal,
    setShowDownloadModal,
    downloadModelName,
    setDownloadModelName,
    downloadModelSavePath,
    setDownloadModelSavePath,
    hfMirror,
    setHfMirror,
    downloading,
    downloadProgress,
    downloadSizeBytes,
    downloadTotalSizeBytes,
    downloadStalledSecs,
    activeDownloadName,
    cliStatus,
    checkingCli,
    installingCli,
    installMessage,
    handleInstallCli,
    handleDownloadModel,
    handleCancelDownload,
  };
}
