import { memo, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Cpu, MemoryStick, HardDrive, CircuitBoard } from 'lucide-react';
import { fmtPct, formatSize } from '../../utils/format';
import { useHardwareStore } from '../../stores/hardware';
import { useI18n } from '../../i18n/context';
import { AnimatedNumber } from '../ui/AnimatedNumber';
import { ProgressBar } from '../ui/ProgressBar';
import type { GPUInfo } from '../../types';

interface HardwareCardsProps {
  sshConnected: boolean;
}

// Theoretical unidirectional bandwidth per lane (MB/s) by PCIe generation
// (128b/130b encoding) — the 100% reference for the Rx/Tx progress bars.
const PCIE_MBS_PER_LANE: Record<number, number> = { 1: 250, 2: 500, 3: 985, 4: 1969, 5: 3938 };

function pcieMaxMbs(gpu: GPUInfo): number {
  const perLane = PCIE_MBS_PER_LANE[gpu.pcie_gen ?? 0] ?? 1969;
  const lanes = gpu.pcie_width && gpu.pcie_width > 0 ? gpu.pcie_width : 8;
  return perLane * lanes;
}

// Audit 2026-10-22 FE-7: frames normally arrive every ~0.5–2 s (the backend
// broadcaster polls at 0.5 s). 10 s without a frame while SSH is still
// connected means the feed stalled (a deployment task occupies SSH so the
// probe cannot run) — the frozen values must not keep looking "live".
const HW_STALE_MS = 10_000;

function formatClock(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// Bar colors (user ruling 2026-10-21): core / memory green, PCIe Rx/Tx blue
// (default bg-accent), power / temp amber (bg-warning).
export const HardwareCards = memo(function HardwareCards({ sshConnected }: HardwareCardsProps) {
  const gpus = useHardwareStore(s => s.gpus);
  const cpu = useHardwareStore(s => s.cpu);
  const memory = useHardwareStore(s => s.memory);
  const disk = useHardwareStore(s => s.disk);
  const lastUpdated = useHardwareStore(s => s.lastUpdated);
  const { t } = useI18n();

  // FE-7: a slow clock so the stale indicator appears/disappears (and its
  // "last updated" time ages) without waiting for the next data frame.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!sshConnected) return;
    const id = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(id);
  }, [sshConnected]);
  const stale = sshConnected && lastUpdated != null && now - lastUpdated > HW_STALE_MS;

  const safeGpus = useMemo(() => (Array.isArray(gpus) ? gpus : []), [gpus]);
  const [lastGpus, setLastGpus] = useState<GPUInfo[]>([]);
  useEffect(() => {
    if (safeGpus.length > 0) setLastGpus(safeGpus);
  }, [safeGpus]);
  const displayGpus = safeGpus.length > 0 ? safeGpus : lastGpus;
  const live = sshConnected && safeGpus.length > 0;
  const totalMemoryUsed = safeGpus.reduce((acc, g) => acc + (g.memory_used_mb ?? 0), 0);
  const totalMemory = safeGpus.reduce((acc, g) => acc + (g.memory_total_mb ?? 0), 0);

  return (
    <>
      {stale && lastUpdated != null && (
        <div className="mb-2 flex items-center gap-1.5 text-xs text-warning">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span>{t('Hardware data not updating — last updated {time}', { time: formatClock(lastUpdated) })}</span>
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-bg-card rounded-xl p-4 border border-border shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <Cpu className="w-4 h-4 text-accent" />
            <span className="text-sm font-medium">{t('CPU')}</span>
          </div>
          <div className="text-base font-mono font-bold text-center tabular-nums">{sshConnected && cpu ? <AnimatedNumber value={cpu.usage ?? 0} format={(v) => `${fmtPct(v)}%`} /> : 'N/A'}</div>
        </div>
        <div className="bg-bg-card rounded-xl p-4 border border-border shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <MemoryStick className="w-4 h-4 text-accent" />
            <span className="text-sm font-medium">{t('RAM')}</span>
          </div>
          <div className="text-base font-mono font-bold text-center tabular-nums">{sshConnected && memory && memory.total > 0 ? <AnimatedNumber value={memory.used ?? 0} format={(v) => `${formatSize(v)} / ${formatSize(memory.total ?? 0)}`} /> : 'N/A'}</div>
        </div>
        <div className="bg-bg-card rounded-xl p-4 border border-border shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <HardDrive className="w-4 h-4 text-accent" />
            <span className="text-sm font-medium">{t('DISK')}</span>
          </div>
          <div className="text-base font-mono font-bold text-center tabular-nums">{sshConnected && disk && disk.total > 0 ? <AnimatedNumber value={disk.used ?? 0} format={(v) => `${formatSize(v)} / ${formatSize(disk.total ?? 0)}`} /> : 'N/A'}</div>
        </div>
        <div className="bg-bg-card rounded-xl p-4 border border-border shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <CircuitBoard className="w-4 h-4 text-accent" />
            <span className="text-sm font-medium">{t('GPU')}</span>
          </div>
          <div className="text-base font-mono font-bold text-center tabular-nums">
            {live
              ? <AnimatedNumber value={totalMemoryUsed} format={(v) => `${formatSize(v * 1024 * 1024, true)} / ${formatSize(totalMemory * 1024 * 1024, true)}`} />
              : 'N/A'}
          </div>
        </div>
      </div>

      {displayGpus.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {displayGpus.map((gpu) => (
            <div key={gpu.index} className="bg-bg-card rounded-xl p-4 border border-border shadow-md">
              <div className="mb-2">
                {/* Badge shares only the GPU0 row (right-aligned); the name
                    keeps the full card width below it — otherwise the
                    nowrap badge squeezes the flex row and the name wraps
                    even when it fits the card width. */}
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{t('GPU')}{gpu.index}</span>
                  {live && gpu.pcie_gen ? (
                    <span className="font-mono text-xs whitespace-nowrap">{`Gen${gpu.pcie_gen} x${gpu.pcie_width ?? '—'}`}</span>
                  ) : null}
                </div>
                <div className="text-xs text-text-muted mt-1">{gpu.name}</div>
              </div>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-text-muted">{t('Core')}</span>
                  <span className="font-mono">{live ? <AnimatedNumber value={gpu.utilization_gpu ?? 0} format={(v) => `${v.toFixed(0)}%`} /> : 'N/A'}</span>
                </div>
                <ProgressBar value={live ? gpu.utilization_gpu ?? 0 : 0} color="bg-success" />
                <div className="flex justify-between">
                  <span className="text-text-muted">{t('Memory')}</span>
                  <span className="font-mono">{live ? <AnimatedNumber value={gpu.memory_used_mb ?? 0} format={(v) => `${formatSize(v * 1024 * 1024, true)} / ${formatSize((gpu.memory_total_mb ?? 0) * 1024 * 1024, true)}`} /> : 'N/A'}</span>
                </div>
                <ProgressBar value={live ? ((gpu.memory_used_mb ?? 0) / Math.max(gpu.memory_total_mb ?? 1, 1)) * 100 : 0} color="bg-success" />
                <div className="flex justify-between">
                  <span className="text-text-muted">{t('Power')}</span>
                  <span className="font-mono">{live ? <AnimatedNumber value={gpu.power_draw_w ?? 0} format={(v) => `${v.toFixed(0)}W / ${gpu.power_limit_w ?? 0}W`} /> : 'N/A'}</span>
                </div>
                <ProgressBar value={live ? ((gpu.power_draw_w ?? 0) / Math.max(gpu.power_limit_w ?? 1, 1)) * 100 : 0} color="bg-warning" />
                <div className="flex justify-between">
                  <span className="text-text-muted">{t('Temp')}</span>
                  <span className="font-mono">{live ? <AnimatedNumber value={gpu.temperature_c ?? 0} format={(v) => `${v.toFixed(0)}°C`} /> : 'N/A'}</span>
                </div>
                <ProgressBar
                  value={live ? ((gpu.temperature_c ?? 0) - 27) / (87 - 27) * 100 : 0}
                  color="bg-warning"
                />
                <div className="flex justify-between">
                  <span className="text-text-muted">{t('PCIe Rx')}</span>
                  <span className="font-mono">{live && gpu.pcie_rx_mbs != null ? <AnimatedNumber value={gpu.pcie_rx_mbs} format={(v) => `${v.toFixed(0)} MB/s`} /> : 'N/A'}</span>
                </div>
                <ProgressBar value={live && gpu.pcie_rx_mbs != null ? (gpu.pcie_rx_mbs / pcieMaxMbs(gpu)) * 100 : 0} />
                <div className="flex justify-between">
                  <span className="text-text-muted">{t('PCIe Tx')}</span>
                  <span className="font-mono">{live && gpu.pcie_tx_mbs != null ? <AnimatedNumber value={gpu.pcie_tx_mbs} format={(v) => `${v.toFixed(0)} MB/s`} /> : 'N/A'}</span>
                </div>
                <ProgressBar value={live && gpu.pcie_tx_mbs != null ? (gpu.pcie_tx_mbs / pcieMaxMbs(gpu)) * 100 : 0} />
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
});
