import { useEffect, useState } from 'react';
import { benchmarkModel } from '../api/models';
import { errMsgLocalized } from '../stores/model';
import type { BenchmarkResult, ModelInfo } from '../types';

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

// The backend archives the latest benchmark per model on the model itself
// (survives closing this tab). Merge those records into the local history:
// for a model the backend reports, its record wins when it is at least as
// recent as the newest local one.
export function mergeBenchmarkResults(
  models: Pick<ModelInfo, 'id' | 'benchmark'>[],
  local: Record<string, BenchmarkResult[]>,
): Record<string, BenchmarkResult[]> {
  const merged: Record<string, BenchmarkResult[]> = { ...local };
  for (const m of models) {
    const b = m.benchmark;
    if (!b) continue;
    const backendTs = Date.parse(b.at);
    if (!Number.isFinite(backendTs)) continue;
    const localTs = merged[m.id]?.[0]?.timestamp;
    if (localTs !== undefined && localTs > backendTs) continue;
    merged[m.id] = [{
      tokens_per_second: b.tokens_per_second,
      prompt_tokens: b.prompt_tokens,
      completion_tokens: b.completion_tokens,
      elapsed_seconds: b.elapsed_seconds,
      timestamp: backendTs,
    }];
  }
  return merged;
}
