import { memo } from 'react';
import { Folder, FolderDown, RefreshCw, Loader2, Play, Square, Settings, Trash2, Gauge, HardDrive, AlertCircle, AlertTriangle, Copy, Globe, Key } from 'lucide-react';
import { formatSize, fmtNum } from '../../utils/format';
import { copyText } from '../../utils/clipboard';
import { ModelStatus } from '../../types';
import type { ModelInfo, BenchmarkResult, DownloadProgressInfo } from '../../types';
import { useI18n } from '../../i18n';
import { IconButton } from '../ui/IconButton';
import { ProgressBar } from '../ui/ProgressBar';
import { showToast } from '../ui/toast';

interface ModelListProps {
  models: ModelInfo[];
  loading: boolean;
  refreshing: boolean;
  scanMessage: { type: 'success' | 'error'; text: string } | null;
  error: string | null;
  starting: boolean;
  deletingId: string | null;
  stoppingId: string | null;
  sshConnected: boolean;
  benchmarkResults: Record<string, BenchmarkResult[]>;
  onRefresh: () => void;
  onStart: (modelId: string) => void;
  onStop: (modelId: string) => void;
  onOpenLaunchConfig: (modelId: string, modelName: string, modelPath: string) => void;
  onDelete: (modelId: string, modelName: string) => void;
  onOpenDownload: () => void;
  onOpenBenchmark: () => void;
  download: DownloadProgressInfo | null;
  onCancelDownload: () => void;
  openaiEndpoint: string | null;
  openaiApiKey: string | null;
}

function ModelApiInfo({ endpoint, apiKey }: { endpoint: string | null; apiKey: string | null }) {
  const { t } = useI18n();
  const copy = (text: string) => {
    void copyText(text).then((ok) => {
      showToast(ok ? t('Copied') : t('Copy failed'), ok ? 'info' : 'error');
    });
  };
  return (
    <div className="mt-1 space-y-1">
      {endpoint && (
        <div className="flex items-center gap-1.5 text-xs font-mono text-text-muted" title={t('OpenAI Endpoint')}>
          <Globe className="w-3 h-3 shrink-0" />
          <span className="truncate">{endpoint}</span>
          <IconButton size="xs" ariaLabel={t('Copy OpenAI endpoint')} onClick={() => copy(endpoint)}>
            <Copy className="w-3 h-3 text-text-muted" />
          </IconButton>
        </div>
      )}
      {apiKey && (
        <div className="flex items-center gap-1.5 text-xs font-mono text-text-muted">
          <Key className="w-3 h-3 shrink-0" />
          <span className="truncate">{apiKey}</span>
          <IconButton size="xs" ariaLabel={t('Copy API Key')} onClick={() => copy(apiKey)}>
            <Copy className="w-3 h-3 text-text-muted" />
          </IconButton>
        </div>
      )}
    </div>
  );
}

export const ModelList = memo(function ModelList({
  models, loading, refreshing, scanMessage, error, starting, deletingId, stoppingId,
  sshConnected, benchmarkResults, openaiEndpoint, openaiApiKey,
  onRefresh, onStart, onStop, onOpenLaunchConfig, onDelete, onOpenDownload, onOpenBenchmark,
  download, onCancelDownload,
}: ModelListProps) {
  const { t } = useI18n();
  const opInFlight = deletingId !== null || stoppingId !== null;
  return (
    <div className="bg-bg-card rounded-xl border border-border shadow-md">
      <div className="flex items-center justify-between p-3 border-b border-border">
        <h2 className="text-sm font-medium flex items-center gap-2">
          <Folder className="w-4 h-4" /> {t('Models')}
        </h2>
        <div className="flex items-center gap-2">
          {scanMessage && (
            <span className={`text-xs ${scanMessage.type === 'success' ? 'text-success' : 'text-danger'}`}>
              {scanMessage.text}
            </span>
          )}
          <IconButton
            size="xs"
            ariaLabel={!sshConnected ? t('Model Download (SSH disconnected)') : t('Model Download')}
            onClick={onOpenDownload}
            disabled={!sshConnected}
          >
            <FolderDown className="w-3.5 h-3.5" />
          </IconButton>
          <IconButton
            size="xs"
            ariaLabel={!sshConnected ? t('Benchmark (SSH disconnected)') : t('Benchmark')}
            onClick={onOpenBenchmark}
            disabled={!sshConnected}
          >
            <Gauge className="w-3.5 h-3.5" />
          </IconButton>
          <IconButton
            size="xs"
            ariaLabel={!sshConnected ? t('Refresh (SSH disconnected)') : t('Refresh')}
            onClick={onRefresh}
            disabled={refreshing || !sshConnected}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </IconButton>
        </div>
      </div>
      {error && (
        <div className="flex items-center gap-2 px-4 py-2 bg-danger/10 border-b border-danger/30 text-danger text-xs">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span>{t('Sync failed: {error}', { error })}</span>
        </div>
      )}
      <div className="divide-y divide-border">
        {download && (
          <div className="p-4 bg-accent/5 flex items-center justify-between">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin text-accent shrink-0" />
                <span className="font-medium truncate">{download.name}</span>
                {download.stalledSecs != null && (
                  <span className="text-xs text-warning flex items-center gap-1 shrink-0">
                    <AlertTriangle className="w-3.5 h-3.5" />
                    {t('Download stalled (no progress for {mins} min)', { mins: Math.floor(download.stalledSecs / 60) })}
                  </span>
                )}
                <span className="text-xs font-mono text-text-muted ml-auto shrink-0">
                  {formatSize(download.sizeBytes)}{download.totalSizeBytes > 0 ? ` / ${formatSize(download.totalSizeBytes)}` : ''} · {download.progress}%
                </span>
              </div>
              <ProgressBar
                value={download.progress}
                color={download.stalledSecs != null ? 'bg-warning' : 'bg-accent'}
                className="mt-1"
              />
            </div>
            <div className="flex items-center gap-1 ml-4 shrink-0">
              <IconButton
                size="sm"
                ariaLabel={t('Cancel download')}
                onClick={onCancelDownload}
              >
                <Square className="w-4 h-4 text-danger" />
              </IconButton>
            </div>
          </div>
        )}
        {models.length === 0 && (
          <div className="p-8 text-center text-text-muted">
            {loading ? <Loader2 className="w-6 h-6 animate-spin mx-auto" /> : !sshConnected ? t('Waiting for SSH connection...') : t('No models found. Click refresh to scan.')}
          </div>
        )}
        {models.map((model) => (
          <div key={model.id} className="p-4 flex items-center justify-between">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span
                  className={`w-2 h-2 rounded-full shrink-0 ${
                    model.status === ModelStatus.RUNNING ? 'bg-success' :
                    model.status === ModelStatus.LOADING ? 'bg-warning animate-pulse' :
                    model.status === ModelStatus.FAILED ? 'bg-danger' :
                    model.status === ModelStatus.DOWNLOADED ? 'bg-info' : 'bg-text-muted'
                  }`}
                />
                <span className="font-medium truncate">{model.name}</span>
                <IconButton
                  size="xs"
                  ariaLabel={t('Copy model name')}
                  onClick={() => {
                    void copyText(model.name).then((ok) => {
                      showToast(ok ? t('Copied') : t('Copy failed'), ok ? 'info' : 'error');
                    });
                  }}
                >
                  <Copy className="w-3 h-3 text-text-muted" />
                </IconButton>
              </div>
              <div className="flex items-center gap-3 mt-1 text-xs">
                {model.size_bytes && model.size_bytes > 0 && (
                  <span className="flex items-center gap-1 text-text-muted shrink-0">
                    <HardDrive className="w-3 h-3" />
                    <span className="font-mono">{formatSize(model.size_bytes ?? 0)}</span>
                  </span>
                )}
                {(() => {
                  const latest = (benchmarkResults[model.id] || []).slice(-1)[0];
                  return latest
                    ? <span
                        className="text-text-muted flex items-center gap-0.5 shrink-0"
                        title={t('Benchmark tok/s (single 128-token run, server-side token count)')}
                    >
                      <Gauge className="w-3 h-3" />{fmtNum(latest.tokens_per_second)} tok/s
                    </span>
                    : <span className="text-text-muted shrink-0">{t('No record')}</span>;
                })()}
              </div>
              {model.status === ModelStatus.RUNNING && (openaiEndpoint || openaiApiKey) && (
                <ModelApiInfo endpoint={openaiEndpoint} apiKey={openaiApiKey} />
              )}
            </div>
            <div className="flex items-center gap-1 ml-4">
              {(!sshConnected ||
                model.status === ModelStatus.STOPPED ||
                model.status === ModelStatus.DOWNLOADED ||
                model.status === ModelStatus.FAILED) && (
                <IconButton
                  size="sm"
                  ariaLabel={!sshConnected ? t('Start (SSH disconnected)') : t('Start')}
                  onClick={() => onStart(model.id)}
                  disabled={starting || opInFlight || !sshConnected}
                >
                  <Play className="w-4 h-4 text-success" />
                </IconButton>
              )}
              {sshConnected && (model.status === ModelStatus.RUNNING || model.status === ModelStatus.LOADING) && (
                <IconButton
                  size="sm"
                  ariaLabel={model.status === ModelStatus.LOADING ? t('Cancel') : t('Stop')}
                  onClick={() => onStop(model.id)}
                  disabled={opInFlight}
                >
                  {stoppingId === model.id
                    ? <Loader2 className="w-4 h-4 animate-spin text-warning" />
                    : <Square className="w-4 h-4 text-warning" />}
                </IconButton>
              )}
              <IconButton
                size="sm"
                ariaLabel={t('Launch Config')}
                onClick={() => onOpenLaunchConfig(model.id, model.name, model.path)}
                disabled={opInFlight}
              >
                <Settings className="w-4 h-4 text-text-muted" />
              </IconButton>
              <IconButton
                size="sm"
                ariaLabel={!sshConnected ? t('Delete (SSH disconnected)') : t('Delete')}
                onClick={() => onDelete(model.id, model.name)}
                disabled={!sshConnected || opInFlight}
              >
                {deletingId === model.id
                  ? <Loader2 className="w-4 h-4 animate-spin text-danger" />
                  : <Trash2 className="w-4 h-4 text-danger" />}
              </IconButton>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
});
