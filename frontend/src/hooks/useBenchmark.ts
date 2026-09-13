import { useEffect, useState } from 'react';
import { benchmarkModel } from '../api/models';
import { errMsgLocalized } from '../stores/model';
import { ModelStatus, type BenchmarkResult, type ModelStatusValue } from '../types';

const BENCHMARK_KEY = 'vllm_benchmark_results';

function persistBenchmarkResults(results: Record<string, BenchmarkResult[]>) {
  try { localStorage.setItem(BENCHMARK_KEY, JSON.stringify(results)); }
  catch {  }
}

export function useBenchmark() {
  const [showBenchmarkModal, setShowBenchmarkModal] = useState(false);
  const loadResults = (): Record<string, BenchmarkResult[]> => {
    try {
      const stored = localStorage.getItem(BENCHMARK_KEY);
      return stored ? JSON.parse(stored) : {};
    } catch { return {}; }
  };
  const [benchmarkResults, setBenchmarkResults] = useState<Record<string, BenchmarkResult[]>>(() => loadResults());
  const [benchmarkingIds, setBenchmarkingIds] = useState<Set<string>>(new Set());
  const [benchmarkErrors, setBenchmarkErrors] = useState<Record<string, string>>({});
  useEffect(() => { persistBenchmarkResults(benchmarkResults); }, [benchmarkResults]);

  const handleBenchmark = async (modelId: string) => {
    if (benchmarkingIds.has(modelId)) return;
    setBenchmarkingIds(prev => new Set(prev).add(modelId));
    setBenchmarkErrors(prev => {
      if (!(modelId in prev)) return prev;
      const next = { ...prev };
      delete next[modelId];
      return next;
    });
    try {
      const result = await benchmarkModel(modelId);
      setBenchmarkResults(prev => ({ ...prev, [modelId]: [{ ...result, timestamp: Date.now() }] }));
    } catch (error: unknown) {
      const msg = errMsgLocalized(error) || String(error);
      setBenchmarkErrors(prev => ({ ...prev, [modelId]: msg }));
    } finally {
      setBenchmarkingIds(prev => {
        const next = new Set(prev);
        next.delete(modelId);
        return next;
      });
    }
  };

  return { benchmarkResults, benchmarkingIds, benchmarkErrors, handleBenchmark, showBenchmarkModal, setShowBenchmarkModal };
}

export const AUTO_BENCH_PENDING_KEY = 'vllm_auto_bench_pending';
const AUTO_BENCH_PENDING_TTL_MS = 30 * 60 * 1000;

export function saveAutoBenchPending(modelId: string): void {
  try {
    localStorage.setItem(AUTO_BENCH_PENDING_KEY, JSON.stringify({ modelId, at: Date.now() }));
  } catch {  }
}

export function readAutoBenchPending(): string | null {
  try {
    const raw = localStorage.getItem(AUTO_BENCH_PENDING_KEY);
    if (!raw) return null;
    let p: { modelId?: unknown; at?: unknown };
    try {
      p = JSON.parse(raw) as { modelId?: unknown; at?: unknown };
    } catch {
      localStorage.removeItem(AUTO_BENCH_PENDING_KEY);
      return null;
    }
    if (typeof p.modelId !== 'string') {
      localStorage.removeItem(AUTO_BENCH_PENDING_KEY);
      return null;
    }
    if (typeof p.at !== 'number' || Date.now() - p.at > AUTO_BENCH_PENDING_TTL_MS) {
      localStorage.removeItem(AUTO_BENCH_PENDING_KEY);
      return null;
    }
    return p.modelId;
  } catch {
    return null;
  }
}

export function clearAutoBenchPending(): void {
  try {
    localStorage.removeItem(AUTO_BENCH_PENDING_KEY);
  } catch {  }
}

export type AutoBenchAction = 'fire' | 'wait';

export function autoBenchAction(
  status: ModelStatusValue | undefined,
  benchmarking: boolean,
): AutoBenchAction {
  if (status === ModelStatus.RUNNING && !benchmarking) return 'fire';
  return 'wait';
}
