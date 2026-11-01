import { describe, expect, it } from 'vitest';
import { lightColor, lightText, type LightState } from './lightStatus';
import type { ProbeErrorCode } from '../types';

// Identity t() with {var} interpolation, mirroring the i18n context shape.
const t = (key: string, vars?: Record<string, string | number>) =>
  vars ? key.replace(/\{(\w+)\}/g, (_, k: string) => String(vars[k] ?? '')) : key;

const light = (over: Partial<LightState>): LightState => ({
  net: 'up', code: null, connected: true, probed: true, ...over,
});

describe('lightColor', () => {
  it('is red on the bootstrap render (nothing probed yet)', () => {
    expect(lightColor(light({ probed: false }))).toBe('bg-danger');
  });

  it('is red when the network layer is down', () => {
    expect(lightColor(light({ net: 'down', connected: false, code: 'ping' }))).toBe('bg-danger');
  });

  it('is yellow while degraded (1–2 ping strikes)', () => {
    expect(lightColor(light({ net: 'degraded', code: 'ping' }))).toBe('bg-warning');
  });

  it('is yellow when the network is up but the service fails', () => {
    expect(lightColor(light({ connected: false, code: 'auth' }))).toBe('bg-warning');
  });

  it('is green (pulsing) when network and service are both ok', () => {
    expect(lightColor(light({}))).toBe('bg-success animate-pulse');
  });
});

describe('lightText', () => {
  it('shows "Disconnected" before the first probe arrives', () => {
    expect(lightText(light({ probed: false }), t)).toBe('Disconnected');
  });

  it('shows the network error for SSH/BMC when ping failed', () => {
    expect(lightText(light({ net: 'down', code: 'ping', connected: false }), t)).toBe('Ping failed');
    expect(lightText(light({ net: 'degraded', code: 'ping' }), t)).toBe('Ping failed');
  });

  it('shows the specific TCP error for the API light', () => {
    expect(lightText(light({ net: 'down', code: 'timeout', connected: false }), t)).toBe('Connection timeout');
    expect(lightText(light({ net: 'down', code: 'refused', connected: false }), t)).toBe('Port refused');
  });

  it('shows "Connected" when the service probe passed', () => {
    expect(lightText(light({}), t)).toBe('Connected');
  });

  it('maps service error codes to specific labels (no "Disconnected" prefix)', () => {
    const cases: [ProbeErrorCode, string][] = [
      ['auth', 'Auth failed'],
      ['no_response', 'No response'],
      ['not_configured', 'Not configured'],
      ['http:501', 'HTTP 501'],
      ['http:503', 'HTTP 503'],
    ];
    for (const [code, label] of cases) {
      expect(lightText(light({ connected: false, code }), t)).toBe(label);
    }
  });

  it('falls back to "Disconnected" for unknown codes', () => {
    expect(lightText(light({ connected: false, code: 'weird' }), t)).toBe('Disconnected');
    expect(lightText(light({ connected: false, code: null }), t)).toBe('Disconnected');
  });
});
