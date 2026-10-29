export function formatSize(bytes: number | null | undefined, keepTrailingZero = false): string {
  if (bytes == null || isNaN(bytes)) return 'N/A';
  if (bytes <= 0) return '0 B';

  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.max(0, Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1));
  const value = bytes / Math.pow(1024, i);

  const s = value.toFixed(i === 0 ? 0 : 1);
  return `${keepTrailingZero ? s : s.replace(/\.0$/, '')} ${units[i]}`;
}

export function fmtNum(v: number): string {
  const s = v.toFixed(1);
  return s.endsWith('.0') ? s.slice(0, -2) : s;
}

export function fmtPct(v: number): string {
  const s = v.toFixed(1);
  return s === '0.0' || s === '100.0' ? s.slice(0, -2) : s;
}

export function fmtInt(v: number): string {
  return v.toFixed(0);
}

// "YYYY-MM-DD HH:mm" in local time; the backend sends naive local datetimes
// (no timezone suffix), which Date parses as local — matching server time.
export function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return 'N/A';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
