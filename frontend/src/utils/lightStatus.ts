import type { NetState, ProbeErrorCode } from '../types';

// Header-light state. The light color is the NETWORK layer (fresh per
// request on the backend: ICMP ping 2-strike debounce for SSH/BMC, TCP 8000
// for the API); the text is the specific SERVICE-layer error. No hover
// tooltips: the label carries the whole message.
export interface LightState {
  net: NetState;
  code: ProbeErrorCode | null | undefined;
  connected: boolean;
  /** False until the first probe response arrives (bootstrap render). */
  probed?: boolean;
}

export function lightColor(s: LightState): string {
  if (!s.probed) return 'bg-danger';
  if (s.net === 'down') return 'bg-danger';
  if (s.net === 'degraded' || !s.connected) return 'bg-warning';
  return 'bg-success animate-pulse';
}

export function lightText(
  s: LightState,
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  if (!s.probed) return t('Disconnected');
  if (s.net !== 'up') {
    // SSH/BMC: ping failed. API: TCP refused/timeout.
    if (s.code === 'timeout') return t('Connection timeout');
    if (s.code === 'refused') return t('Port refused');
    return t('Ping failed');
  }
  if (s.connected) return t('Connected');
  switch (s.code) {
    case 'not_configured':
      return t('Not configured');
    case 'auth':
      return t('Auth failed');
    case 'timeout':
      return t('Connection timeout');
    case 'refused':
      return t('Port refused');
    case 'no_response':
      return t('No response');
    default:
      if (typeof s.code === 'string' && s.code.startsWith('http:')) {
        return t('HTTP {code}', { code: s.code.slice(5) });
      }
      return t('Disconnected');
  }
}
