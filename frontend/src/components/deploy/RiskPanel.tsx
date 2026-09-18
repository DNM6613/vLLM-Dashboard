import { AlertTriangle } from 'lucide-react';
import { useI18n } from '../../i18n';

const RISK_KEYS = [
  'Risk 1: driver change requires reboot',
  'Risk 3: open kernel driver',
  'Risk 5: driver purge',
];

export function RiskPanel() {
  const { t } = useI18n();
  return (
    <section className="border border-warning/40 bg-warning/5 rounded-lg p-4">
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle className="w-4 h-4 text-warning" />
        <h4 className="text-sm font-medium">{t('Risks and Limitations')}</h4>
      </div>
      <ol className="list-decimal list-inside space-y-1 text-xs text-text-muted">
        {RISK_KEYS.map((key) => (
          <li key={key}>{t(key)}</li>
        ))}
      </ol>
    </section>
  );
}
