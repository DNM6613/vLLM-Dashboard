import { memo } from 'react';
import { Folder, FolderDown, RefreshCw, Loader2, Play, Square, Settings, Trash2, Gauge, HardDrive, AlertCircle } from 'lucide-react';
import { formatSize, fmtNum } from '../../utils/format';
import { ModelStatus } from '../../types';
import type { ModelInfo, BenchmarkResult } from '../../types';
import { useI18n } from '../../i18n';
import { IconButton } from '../ui/IconButton';

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
}

export const ModelList = memo(function ModelList({
  models, loading, refreshing, scanMessage, error, starting, deletingId, stoppingId,
  sshConnected, benchmarkResults,
  onRefresh, onStart, onStop, onOpenLaunchConfig, onDelete, onOpenDownload, onOpenBenchmark,
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
