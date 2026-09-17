import { useState } from 'react';
import { ChevronDown, Globe } from 'lucide-react';
import { useI18n } from '../../i18n';
import { Button } from '../ui/Button';

interface MirrorPanelProps {
  pypi: string;
  hf: string;
  saving: boolean;
  onSave: (mirrors: { pypi?: string; hf?: string }) => Promise<boolean>;
}

const PYPI_PRESETS = [
  { value: '', label: 'pypi.org (official)' },
  { value: 'https://pypi.tuna.tsinghua.edu.cn/simple/', label: 'Tsinghua' },
  { value: 'https://mirrors.aliyun.com/pypi/simple/', label: 'Aliyun' },
];

const FIELD_CLASS = 'px-3 py-1.5 bg-bg rounded-lg border border-border text-text focus:border-accent focus:outline-none font-mono text-sm';

export function MirrorPanel({ pypi, hf, saving, onSave }: MirrorPanelProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [pypiPick, setPypiPick] = useState(PYPI_PRESETS.some((p) => p.value === pypi) ? pypi : 'custom');
  const [pypiCustom, setPypiCustom] = useState(PYPI_PRESETS.some((p) => p.value === pypi) ? '' : pypi);
  const [hfDraft, setHfDraft] = useState(hf);
  const [busy, setBusy] = useState(false);

  const effPypi = pypiPick === 'custom' ? pypiCustom.trim() : pypiPick;

  const handleSave = async () => {
    setBusy(true);
    await onSave({ pypi: effPypi, hf: hfDraft.trim() });
    setBusy(false);
  };

  return (
    <section className="border border-border rounded-lg">
      <button
        onClick={() => setOpen((s) => !s)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-bg-hover/40 transition-colors rounded-lg"
      >
        <span className="flex items-center gap-2 text-sm font-medium">
          <Globe className="w-4 h-4 text-accent" />
          {t('Mirror sources')}
          {pypi || hf ? (
            <span className="text-[11px] text-text-muted font-normal">
              {PYPI_PRESETS.find((p) => p.value === pypi)?.label ?? (pypi ? t('custom') : t('official'))}
              {hf ? ` · ${hf}` : ''}
            </span>
          ) : null}
        </span>
        <ChevronDown className={`w-4 h-4 text-text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-3">
          <div className="text-xs text-text-muted">
            {t('Applied to vLLM / torch installs to avoid download timeouts.')}
          </div>
          <div>
            <label className="block text-xs text-text-muted mb-1" htmlFor="mirror-pypi">
              PyPI
            </label>
            <div className="flex gap-2">
              <select
                id="mirror-pypi"
                value={pypiPick}
                onChange={(e) => setPypiPick(e.target.value)}
                className={`${FIELD_CLASS} flex-1 !font-sans`}
              >
                {PYPI_PRESETS.map((p) => <option key={p.value} value={p.value}>{t(p.label)}</option>)}
                <option value="custom">{t('Custom')}</option>
              </select>
              {pypiPick === 'custom' && (
                <input
                  type="text"
                  value={pypiCustom}
                  onChange={(e) => setPypiCustom(e.target.value)}
                  placeholder="https://mirror.example.com/pypi/simple/"
                  className={`${FIELD_CLASS} flex-1`}
                />
              )}
            </div>
          </div>
          <div>
            <label className="block text-xs text-text-muted mb-1" htmlFor="mirror-hf">
              HuggingFace
            </label>
            <input
              id="mirror-hf"
              type="text"
              value={hfDraft}
              onChange={(e) => setHfDraft(e.target.value)}
              placeholder="https://hf-mirror.com"
              className={`${FIELD_CLASS} w-full`}
            />
          </div>
          <div className="flex justify-end">
            <Button size="sm" variant="primary" onClick={handleSave} disabled={busy || saving}>
              {t('Save')}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
