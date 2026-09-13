import { memo } from 'react';
import type { SoftwareInfo } from '../../types';
import { useHardwareStore } from '../../stores/hardware';
import { useI18n } from '../../i18n';

const FIELDS: { key: keyof SoftwareInfo; labelKey: string }[] = [
  { key: 'os', labelKey: 'OS' },
  { key: 'gpu_driver', labelKey: 'GPU Driver' },
  { key: 'cuda_toolkit', labelKey: 'CUDA' },
  { key: 'vllm', labelKey: 'vLLM' },
];

const VERSION_BADGE = 'px-1.5 py-0.5 rounded bg-bg-hover text-text-muted text-xs font-mono';

interface SoftwareVersionBadgesProps {
  sshConnected: boolean;
}

export const SoftwareVersionBadges = memo(function SoftwareVersionBadges({ sshConnected }: SoftwareVersionBadgesProps) {
  const software = useHardwareStore(s => s.software);
  const { t } = useI18n();

  if (!sshConnected || !software) {
    return null;
  }

  return (
    <div className="hidden md:flex md:flex-wrap items-center gap-x-4 gap-y-1">
      {FIELDS.map(({ key, labelKey }) => {
        const value = software[key] || 'N/A';
        return (
          <div key={key} className="flex items-center gap-1.5 min-w-0">
            <span className="text-xs font-medium text-text-muted shrink-0">{t(labelKey)}</span>
            <span className={`${VERSION_BADGE} truncate`} title={value}>
              {value}
            </span>
          </div>
        );
      })}
    </div>
  );
});
