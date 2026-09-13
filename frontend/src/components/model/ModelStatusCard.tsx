import { memo } from 'react';
import { Activity } from 'lucide-react';
import { fmtInt, fmtNum, fmtPct } from '../../utils/format';
import { useI18n } from '../../i18n';
import { useModelStatusStore } from '../../stores/modelStatus';
import { AnimatedNumber } from '../ui/AnimatedNumber';

interface StatProps {
  label: string;
  num?: number | null;
  num2?: number | null;
  num3?: number | null;
  allowPartial?: boolean;
  zeroMissingNum?: boolean;
  format?: (v: number) => string;
  suffix?: string;
  perSegment?: boolean;
}

function Stat({ label, num, num2, num3, allowPartial = false, zeroMissingNum = false, format = fmtNum, suffix = '', perSegment = false }: StatProps) {
  const isCombined = num2 !== undefined;
  const tail = perSegment ? '' : suffix;
  const segFmt = (v: number) => format(v) + (perSegment ? suffix : '');
  const pairEl = !isCombined
    ? null
    : num != null && num2 != null
      ? (
        <>
          <AnimatedNumber value={num} format={segFmt} />
          /
          <AnimatedNumber value={num2} format={segFmt} />
          {tail}
        </>
      )
      : allowPartial && (num != null || num2 != null)
        ? (
          <>
            {num != null
              ? <AnimatedNumber value={num} format={segFmt} />
              : zeroMissingNum
                ? <AnimatedNumber value={0} format={segFmt} />
                : 'N/A'}
            /
            {num2 != null
              ? <AnimatedNumber value={num2} format={segFmt} />
              : 'N/A'}
            {tail}
          </>
        )
        : 'N/A';
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="min-w-0 truncate text-text-muted">{label}</span>
        <span className="shrink-0 whitespace-nowrap font-mono text-sm text-text">
          {num3 !== undefined ? (
            pairEl === 'N/A' && num3 == null ? (
              'N/A'
            ) : (
              <>
                {pairEl ?? 'N/A'}
                {' · '}
                {num3 != null
                  ? <AnimatedNumber value={num3} format={format} />
                  : 'N/A'}
              </>
            )
          ) : isCombined ? (
            pairEl
          ) : num != null ? (
            <AnimatedNumber value={num} format={(v) => `${format(v)}${suffix}`} />
          ) : 'N/A'}
        </span>
      </div>
    </div>
  );
}

export const ModelStatusCard = memo(function ModelStatusCard() {
  const model = useModelStatusStore(s => s.model);
  const { t } = useI18n();

  const loading = model === null;

  return (
    <div className="bg-bg-card rounded-xl border border-border shadow-md">
      <div className="flex items-center justify-between p-3 border-b border-border">
        <h2 className="text-sm font-medium flex items-center gap-2">
          <Activity className="w-4 h-4 text-accent" /> {t('Model Status')}
        </h2>
      </div>
      {loading ? (
        <div className="px-4 py-3 text-sm text-text-muted">
          {t('Loading…')}
        </div>
      ) : (
        <div className="px-4 py-3">
          <div className="grid grid-cols-1 gap-y-3">
            <Stat
              label={t('Running/Queue · Preemptions')}
              num={model?.running_requests}
              num2={model?.waiting_requests}
              num3={model?.preemptions_total}
              format={fmtInt}
            />
            <Stat label={t('Prompt Throughput (tok/s)')} num={model?.prompt_tokens_per_s} format={fmtInt} />
            <Stat label={t('Generation Throughput (tok/s)')} num={model?.generation_tokens_per_s} format={fmtInt} />
            <Stat
              label={t('KV Cache')}
              num={model?.kv_cache_usage_pct}
              format={fmtPct}
              suffix="%"
            />
            <Stat label={t('Avg TTFT (s)')} num={model?.avg_ttft_s} />
            <Stat label={t('Avg TPOT (ms)')} num={model?.avg_tpot_ms} />
            <Stat label={t('Avg E2E Latency (s)')} num={model?.avg_e2e_latency_s} />
            <Stat
              label={t('MTP Hit Rate (Realtime/Cumulative)')}
              num={model?.mtp_hit_rate_pct}
              num2={model?.mtp_hit_rate_cumulative_pct}
              allowPartial
              zeroMissingNum
              format={fmtPct}
              suffix="%"
              perSegment
            />
            <Stat
              label={t('Prefix Cache Hit Rate (Realtime/Cumulative)')}
              num={model?.prefix_cache_hit_rate_pct}
              num2={model?.prefix_cache_hit_rate_cumulative_pct}
              allowPartial
              zeroMissingNum
              format={fmtPct}
              suffix="%"
              perSegment
            />
          </div>
        </div>
      )}
    </div>
  );
});
