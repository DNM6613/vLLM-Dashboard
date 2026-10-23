import { memo, useState } from 'react';
import { Activity, ChevronDown } from 'lucide-react';
import { fmtInt, fmtNum, fmtPct } from '../../utils/format';
import { useI18n } from '../../i18n/context';
import { useModelStatusStore } from '../../stores/modelStatus';
import { AnimatedNumber } from '../ui/AnimatedNumber';

interface StatProps {
  label: string;
  num?: number | null;
  num2?: number | null;
  allowPartial?: boolean;
  zeroMissingNum?: boolean;
  format?: (v: number) => string;
  suffix?: string;
  perSegment?: boolean;
}

function Stat({ label, num, num2, allowPartial = false, zeroMissingNum = false, format = fmtNum, suffix = '', perSegment = false }: StatProps) {
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
          {isCombined ? (
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

  const [open, setOpen] = useState(false);

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
              label={t('Running/Queue')}
              num={model?.running_requests}
              num2={model?.waiting_requests}
              format={fmtInt}
            />
            <Stat label={t('Prompt Throughput (tok/s)')} num={model?.prompt_tokens_per_s} format={fmtInt} />
            <Stat label={t('Generation Throughput (tok/s)')} num={model?.generation_tokens_per_s} format={fmtInt} />
            <Stat
              label={t('Cumulative Tokens (In/Out)')}
              num={model?.prompt_tokens_total}
              num2={model?.generation_tokens_total}
              allowPartial
              format={fmtInt}
            />
            <Stat
              label={t('KV Cache')}
              num={model?.kv_cache_usage_pct}
              format={fmtPct}
              suffix="%"
            />
            <Stat
              label={t('MTP Hit Rate (Realtime/Average)')}
              num={model?.mtp_hit_rate_pct}
              num2={model?.mtp_hit_rate_cumulative_pct}
              allowPartial
              zeroMissingNum
              format={fmtPct}
              suffix="%"
              perSegment
            />
            <Stat
              label={t('Prefix Cache Hit Rate (Realtime/Average)')}
              num={model?.prefix_cache_hit_rate_pct}
              num2={model?.prefix_cache_hit_rate_cumulative_pct}
              allowPartial
              zeroMissingNum
              format={fmtPct}
              suffix="%"
              perSegment
            />
            <Stat
              label={t('Avg Prefill/Decode (s)')}
              num={model?.avg_prefill_s}
              num2={model?.avg_decode_s}
            />
            {open && (
              <div className="grid grid-cols-1 gap-y-3 mb-3">
                <Stat
                  label={t('Rejections/Preemptions (Total)')}
                  num={model?.admission_rejections_total}
                  num2={model?.num_preemptions_total}
                  format={fmtInt}
                />
                <Stat label={t('Avg TTFT (s)')} num={model?.avg_ttft_s} />
                <Stat label={t('Avg E2E Latency (s)')} num={model?.avg_e2e_latency_s} />
                <Stat label={t('Avg TPOT (ms)')} num={model?.avg_tpot_ms} />
                <Stat label={t('Avg Queue (s)')} num={model?.avg_queue_s} />
              </div>
            )}
            <button
              type="button"
              aria-label="Detailed Metrics"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              className="flex cursor-pointer select-none items-center justify-center text-text-muted hover:text-text"
            >
              <ChevronDown className={`h-3.5 w-3.5 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
});
