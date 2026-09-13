import { describe, expect, it } from 'vitest';
import { fmtInt, fmtNum, fmtPct, formatSize } from './format';

describe('formatSize', () => {
  it('null/NaN → N/A', () => {
    expect(formatSize(null)).toBe('N/A');
    expect(formatSize(undefined)).toBe('N/A');
    expect(formatSize(NaN)).toBe('N/A');
  });

  it('zero → 0 B', () => {
    expect(formatSize(0)).toBe('0 B');
  });

  it('unit boundaries (v2.9.29 数字约定：".0" 显整数)', () => {
    expect(formatSize(1023)).toBe('1023 B');
    expect(formatSize(1024)).toBe('1 KB');
    expect(formatSize(1024 * 1024)).toBe('1 MB');
    expect(formatSize(10 * 1024 ** 3)).toBe('10 GB');
    expect(formatSize(1.5 * 1024 ** 3)).toBe('1.5 GB');
  });

  it('caps at TB for huge values', () => {
    expect(formatSize(2048 * 1024 ** 4)).toBe('2048 TB');
  });

  it('keepTrailingZero=true 保留旧 ".0" 显示（GPU 卡排除项）', () => {
    expect(formatSize(1024, true)).toBe('1.0 KB');
    expect(formatSize(1024 * 1024, true)).toBe('1.0 MB');
    expect(formatSize(10 * 1024 ** 3, true)).toBe('10.0 GB');
    expect(formatSize(2048 * 1024 ** 4, true)).toBe('2048.0 TB');
    expect(formatSize(1.5 * 1024 ** 3, true)).toBe('1.5 GB');
  });

  it('sub-byte sizes (0<bytes<1) clamp to B unit, never undefined', () => {
    for (const bytes of [0.5, 0.1, 0.9]) {
      const out = formatSize(bytes);
      expect(out).not.toContain('undefined');
      expect(out).toMatch(/^\d+(\.\d+)? B$/);
    }
  });
});

describe('fmtNum', () => {
  it('".0" → 整数', () => {
    expect(fmtNum(40)).toBe('40');
    expect(fmtNum(40.0)).toBe('40');
    expect(fmtNum(0)).toBe('0');
  });

  it('非零小数保留 1 位（四舍五入）', () => {
    expect(fmtNum(40.2)).toBe('40.2');
    expect(fmtNum(67.8)).toBe('67.8');
    expect(fmtNum(123.4)).toBe('123.4');
    expect(fmtNum(1.25)).toBe('1.3');
    expect(fmtNum(0.04)).toBe('0');
  });
});

describe('fmtPct', () => {
  it('0 与 100 → 无小数', () => {
    expect(fmtPct(0)).toBe('0');
    expect(fmtPct(100)).toBe('100');
    expect(fmtPct(99.96)).toBe('100');
  });

  it('其余保留 1 位', () => {
    expect(fmtPct(55.5)).toBe('55.5');
    expect(fmtPct(55.0)).toBe('55.0');
    expect(fmtPct(59.6)).toBe('59.6');
  });
});

describe('fmtInt', () => {
  it('非零小数 → 整数（四舍五入）', () => {
    expect(fmtInt(67.8)).toBe('68');
    expect(fmtInt(67.2)).toBe('67');
    expect(fmtInt(123.4)).toBe('123');
    expect(fmtInt(123.5)).toBe('124');
  });

  it('整数 → 原样', () => {
    expect(fmtInt(60)).toBe('60');
    expect(fmtInt(0)).toBe('0');
  });
});
