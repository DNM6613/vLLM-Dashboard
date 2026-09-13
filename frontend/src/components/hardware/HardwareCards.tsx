import { memo, useEffect, useMemo, useState } from 'react';
import { Cpu, MemoryStick, HardDrive, CircuitBoard } from 'lucide-react';
import { fmtPct, formatSize } from '../../utils/format';
import { useHardwareStore } from '../../stores/hardware';
import { useI18n } from '../../i18n';
import { AnimatedNumber } from '../ui/AnimatedNumber';
import { ProgressBar } from '../ui/ProgressBar';
import type { GPUInfo } from '../../types';

interface HardwareCardsProps {
  sshConnected: boolean;
}

export const HardwareCards = memo(function HardwareCards({ sshConnected }: HardwareCardsProps) {
  const gpus = useHardwareStore(s => s.gpus);
  const cpu = useHardwareStore(s => s.cpu);
  const memory = useHardwareStore(s => s.memory);
  const disk = useHardwareStore(s => s.disk);
  const { t } = useI18n();

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
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-bg-card rounded-xl p-4 border border-border shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <Cpu className="w-4 h-4 text-accent" />
            <span className="text-sm font-medium">{t('CPU')}</span>
          </div>
          <div className="text-base font-mono font-bold">{sshConnected && cpu ? <AnimatedNumber value={cpu.usage ?? 0} format={(v) => `${fmtPct(v)}%`} /> : 'N/A'}</div>
          {sshConnected && cpu ? (
            <div className="text-xs text-text-muted mt-1 truncate" title={cpu.model}>
              {cpu.model}
            </div>
          ) : null}
        </div>
        <div className="bg-bg-card rounded-xl p-4 border border-border shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <MemoryStick className="w-4 h-4 text-accent" />
            <span className="text-sm font-medium">{t('RAM')}</span>
          </div>
          <div className="text-base font-mono font-bold">{sshConnected && memory && memory.total > 0 ? <AnimatedNumber value={memory.used ?? 0} format={(v) => `${formatSize(v)} / ${formatSize(memory.total ?? 0)}`} /> : 'N/A'}</div>
        </div>
        <div className="bg-bg-card rounded-xl p-4 border border-border shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <HardDrive className="w-4 h-4 text-accent" />
            <span className="text-sm font-medium">{t('DISK')}</span>
          </div>
          <div className="text-base font-mono font-bold">{sshConnected && disk && disk.total > 0 ? <AnimatedNumber value={disk.used ?? 0} format={(v) => `${formatSize(v)} / ${formatSize(disk.total ?? 0)}`} /> : 'N/A'}</div>
        </div>
        <div className="bg-bg-card rounded-xl p-4 border border-border shadow-md">
          <div className="flex items-center gap-2 mb-2">
            <CircuitBoard className="w-4 h-4 text-accent" />
            <span className="text-sm font-medium">{t('GPU')}</span>
          </div>
          <div className="text-base font-mono font-bold">
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
                <span className="text-sm font-medium">{t('GPU')}{gpu.index}</span>
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
                <ProgressBar value={live ? ((gpu.memory_used_mb ?? 0) / Math.max(gpu.memory_total_mb ?? 1, 1)) * 100 : 0} />
                <div className="flex justify-between">
                  <span className="text-text-muted">{t('Power')}</span>
                  <span className="font-mono">{live ? <AnimatedNumber value={gpu.power_draw_w ?? 0} format={(v) => `${v.toFixed(0)}W / ${gpu.power_limit_w ?? 0}W`} /> : 'N/A'}</span>
                </div>
                <ProgressBar value={live ? ((gpu.power_draw_w ?? 0) / Math.max(gpu.power_limit_w ?? 1, 1)) * 100 : 0} />
                <div className="flex justify-between">
                  <span className="text-text-muted">{t('Temp')}</span>
                  <span className="font-mono">{live ? <AnimatedNumber value={gpu.temperature_c ?? 0} format={(v) => `${v.toFixed(0)}°C`} /> : 'N/A'}</span>
                </div>
                <ProgressBar
                  value={live ? ((gpu.temperature_c ?? 0) - 27) / (87 - 27) * 100 : 0}
                  color={live ? ((gpu.temperature_c ?? 0) < 60 ? 'bg-accent' : (gpu.temperature_c ?? 0) > 80 ? 'bg-danger' : 'bg-warning') : 'bg-border'}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
});
