import type { DriverTag } from '../../api/deployment';
import { useI18n } from '../../i18n';

const TAG_STYLES: Record<string, string> = {
  blue: 'bg-blue-500/15 text-blue-500',
  orange: 'bg-orange-500/15 text-orange-500',
  green: 'bg-green-500/15 text-green-600',
  purple: 'bg-purple-500/15 text-purple-500',
  pink: 'bg-pink-500/15 text-pink-500',
  yellow: 'bg-yellow-500/25 text-yellow-600',
};

export function TagChip({ tag }: { tag: DriverTag }) {
  const { t } = useI18n();
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[11px] leading-none font-medium ${TAG_STYLES[tag.color] ?? 'bg-bg-hover text-text-muted'}`}>
      {t(tag.label)}
    </span>
  );
}
