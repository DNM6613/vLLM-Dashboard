import { Gauge, Loader2 } from 'lucide-react';
import { fmtNum } from '../../utils/format';
import { ModelStatus } from '../../types';
import type { ModelInfo, BenchmarkResult } from '../../types';
import { useI18n } from '../../i18n';
import { Button } from '../ui/Button';
import { EmptyState } from '../ui/EmptyState';
import { Modal } from '../ui/Modal';

interface BenchmarkModalProps {
  models: ModelInfo[];
  benchmarkResults: Record<string, BenchmarkResult[]>;
  benchmarkingIds: Set<string>;
  benchmarkErrors: Record<string, string>;
  onBenchmark: (modelId: string) => void;
  onClose: () => void;
}

export function BenchmarkModal({ models, benchmarkResults, benchmarkingIds, benchmarkErrors, onBenchmark, onClose }: BenchmarkModalProps) {
  const { t } = useI18n();
  const runningModel = models.find(m => m.status === ModelStatus.RUNNING);
  const benchmarking = runningModel ? benchmarkingIds.has(runningModel.id) : false;
  const error = runningModel ? benchmarkErrors[runningModel.id] : undefined;
  const latest = runningModel ? (benchmarkResults[runningModel.id] || []).slice(-1)[0] : undefined;

  return (
    <Modal
      title={t('Token Benchmark')}
      icon={<Gauge className="w-5 h-5 text-accent" />}
      maxWidth="max-w-md"
      onClose={onClose}
    >
      {!runningModel ? (
        <EmptyState
          className="py-8"
          icon={<Gauge className="w-12 h-12 mx-auto mb-3 opacity-30" />}
          title={t('No running model')}
          description={t('Start a model before benchmarking')}
        />
      ) : (
        <div className="space-y-4">
          <div className="bg-bg rounded-lg p-3 border border-border">
            <div className="text-xs text-text-muted">{t('Current Model')}</div>
            <div className="flex items-center gap-2 mt-1">
              <span className="w-2 h-2 rounded-full bg-success" />
              <span className="font-medium truncate">{runningModel.name}</span>
            </div>
          </div>
          {latest && (
            <div className="bg-accent/10 rounded-lg p-4 border border-accent/30 text-center">
              <div className="text-3xl font-mono font-bold text-accent-strong">
                {fmtNum(latest.tokens_per_second)} <span className="text-sm font-normal">tok/s</span>
              </div>
            </div>
          )}
          {error && (
            <div className="text-xs text-danger">{error}</div>
          )}
          <Button
            variant="primary"
            className="w-full justify-center"
            onClick={() => onBenchmark(runningModel.id)}
            disabled={benchmarking}
          >
            {benchmarking ? <><Loader2 className="w-4 h-4 animate-spin" /> {t('Benchmarking…')}</> : <><Gauge className="w-4 h-4" /> {t('Start Benchmark')}</>}
          </Button>
        </div>
      )}
    </Modal>
  );
}
