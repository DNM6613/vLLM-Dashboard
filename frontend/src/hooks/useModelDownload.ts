import { useCallback, useEffect, useRef, useState } from 'react';
import { errMsgLocalized, useModelStore } from '../stores/model';
import { checkCliTools, downloadModel, getDownloadStatus, getInstallStatus, installCliTool } from '../api/models';
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
  const [downloadNotice, setDownloadNotice] = useState<string | null>(null);
  const clearDownloadNotice = useCallback(() => setDownloadNotice(null), []);
  const [activeDownloadName, setActiveDownloadName] = useState('');
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
    setDownloading(true);
    setDownloadProgress(0);
    setActiveDownloadName(repoName);
    setDownloadNotice(null);
    setShowDownloadModal(false);
    try {
      const result = await downloadModel(repoName, downloadModelSavePath.trim(), hfMirror);
      setDownloadModelName('');
      setDownloadModelSavePath('');
      if (result.model_save_path) onSavePathPersisted(result.model_save_path);
      let pollCount = 0;
      const maxPolls = 1200;
      const logFile = result.log_file;
      const pollDownload = async () => {
        pollCount++;
        try {
          const status = await getDownloadStatus(logFile);
          if (!isMountedRef.current) return;
          if (status.status === 'downloading') {
            setDownloadProgress(Math.round(status.progress ?? 0));
            if (pollCount < maxPolls) downloadPollTimeoutRef.current = setTimeout(pollDownload, 3000);
            else {
              setDownloadNotice(tRef.current('Download status polling stopped after 1 hour — the download may still be running in the background. Check the model list or backend logs for its real status.'));
              setDownloading(false); setDownloadProgress(0); setActiveDownloadName(''); fetchModels();
            }
          } else if (status.status === 'complete') {
            setDownloadProgress(100); setDownloading(false);
            setTimeout(() => { if (isMountedRef.current) { setDownloadProgress(0); setActiveDownloadName(''); } }, 2000);
            scanModels().then(() => fetchModels());
          } else if (status.status === 'failed') {
            const reason = status.reason?.slice(0, 300);
            showToast(tRef.current('Download failed') + (reason ? `\n${reason}` : ''));
            setDownloading(false); setDownloadProgress(0); setActiveDownloadName('');
            fetchModels();
          } else {
            setDownloading(false); setDownloadProgress(0); setActiveDownloadName('');
            fetchModels();
          }
        } catch {
          if (!isMountedRef.current) return;
          if (pollCount < maxPolls) downloadPollTimeoutRef.current = setTimeout(pollDownload, 3000);
          else {
            setDownloadNotice(tRef.current('Download status polling stopped after 1 hour — the download may still be running in the background. Check the model list or backend logs for its real status.'));
            setDownloading(false); setDownloadProgress(0); setActiveDownloadName(''); fetchModels();
          }
        }
      };
      downloadPollTimeoutRef.current = setTimeout(pollDownload, 3000);
    } catch (e: unknown) {
      const msg = errMsgLocalized(e);
      showToast(tRef.current('Download request failed') + (msg ? `: ${msg}` : ''));
      setDownloading(false); setDownloadProgress(0); setActiveDownloadName('');
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
    downloadNotice,
    clearDownloadNotice,
    activeDownloadName,
    cliStatus,
    checkingCli,
    installingCli,
    installMessage,
    handleInstallCli,
    handleDownloadModel,
  };
}
