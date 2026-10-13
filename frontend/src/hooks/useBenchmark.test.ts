import { describe, expect, it } from 'vitest';
import { mergeBenchmarkResults } from './useBenchmark';
import type { BenchmarkResult, ModelInfo } from '../types';

const backendAt = '2026-10-13T11:12:14.123456+00:00';
const backendTs = Date.parse(backendAt);

const benchModel = (id: string): Pick<ModelInfo, 'id' | 'benchmark'> => ({
  id,
  benchmark: {
    tokens_per_second: 69.5,
    prompt_tokens: 12,
    completion_tokens: 128,
    elapsed_seconds: 1.85,
    at: backendAt,
  },
});

const localResult = (timestamp: number): BenchmarkResult => ({
  tokens_per_second: 40.2,
  prompt_tokens: 10,
  completion_tokens: 128,
  elapsed_seconds: 3.18,
  timestamp,
});

describe('mergeBenchmarkResults', () => {
  it('uses the backend record when there is no local one', () => {
    const merged = mergeBenchmarkResults([benchModel('m1')], {});
    expect(merged['m1']).toEqual([{
      tokens_per_second: 69.5,
      prompt_tokens: 12,
      completion_tokens: 128,
      elapsed_seconds: 1.85,
      timestamp: backendTs,
    }]);
  });

  it('overwrites the local record when the backend one is newer', () => {
    const local = { m1: [localResult(backendTs - 60_000)] };
    const merged = mergeBenchmarkResults([benchModel('m1')], local);
    expect(merged['m1']![0].tokens_per_second).toBe(69.5);
    expect(merged['m1']![0].timestamp).toBe(backendTs);
  });

  it('keeps the local record when it is newer than the backend one', () => {
    const local = { m1: [localResult(backendTs + 60_000)] };
    const merged = mergeBenchmarkResults([benchModel('m1')], local);
    expect(merged['m1']).toEqual([localResult(backendTs + 60_000)]);
  });

  it('leaves local records untouched for models without a backend benchmark', () => {
    const local = { m1: [localResult(backendTs)] };
    const merged = mergeBenchmarkResults([{ id: 'm1', benchmark: null }], local);
    expect(merged['m1']).toEqual([localResult(backendTs)]);
    const merged2 = mergeBenchmarkResults([{} as Pick<ModelInfo, 'id' | 'benchmark'>], local);
    expect(merged2['m1']).toEqual([localResult(backendTs)]);
  });

  it('does not mutate the local input', () => {
    const local = { m1: [localResult(backendTs - 1)] };
    mergeBenchmarkResults([benchModel('m1')], local);
    expect(local['m1']![0].tokens_per_second).toBe(40.2);
  });
});
