import type { Interp } from '../../i18n/context';

// Values embed live data (GB number, host list, kernel/OS version), so a
// whole-string lookup would never match — translate the static frame via a
// template and keep the data; plain values fall through to a direct lookup.
// Lives in its own module so PreflightCard.tsx stays component-only for
// fast refresh (react-refresh/only-export-components, audit 2026-10-17 A-4).
export function displayPreflightValue(
  item: { key: string; value: string },
  t: (key: string, vars?: Interp) => string,
): string {
  const v = item.value;
  if (item.key === 'disk' && /^[\d.]+ GB free$/.test(v)) {
    return t('{n} GB free', { n: v.replace(' GB free', '') });
  }
  if (item.key === 'network' && v.startsWith('reachable: ')) {
    return t('reachable: {hosts}', { hosts: v.slice('reachable: '.length) });
  }
  return t(v);
}
