import { useState } from 'react';
import { ChevronDown, Info, Lock, XCircle } from 'lucide-react';
import type { CudaInfo } from '../../api/deployment';
import { useI18n } from '../../i18n';
import { Button } from '../ui/Button';

interface CudaTabProps {
  locked: boolean;
  info: CudaInfo | null;
  pickedVersion: string;
  onPickVersion: (v: string) => void;
  installSystem: boolean;
  onInstallSystemChange: (b: boolean) => void;
  onApply: (version: string, installSystem: boolean) => Promise<boolean>;
  busy: boolean;
}

export function CudaTab({
  locked, info, pickedVersion, onPickVersion, installSystem,
  onInstallSystemChange, onApply, busy,
}: CudaTabProps) {
  const { t } = useI18n();
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [customVersion, setCustomVersion] = useState('');
  const [acting, setActing] = useState(false);

  if (locked) {
    return (
      <div className="border border-border rounded-lg p-8 text-center">
        <Lock className="w-6 h-6 text-text-muted mx-auto mb-2" />
        <div className="text-sm text-text-muted">{t('Locked: finish the GPU driver deployment first.')}</div>
      </div>
    );
  }

  if (!info) {
    return <div className="text-xs text-text-muted py-6 text-center">{t('Loading...')}</div>;
  }

  const version = customVersion.trim() || pickedVersion;
  const required = version ? info.min_driver[version] : undefined;
  const incompatible = required != null && info.driver_major != null && info.driver_major < required;
  const selMajor = info.selected_driver_major;
  const selDiffers = selMajor != null && selMajor !== info.driver_major;

  const handleApply = async () => {
    if (!version || incompatible) return;
    setActing(true);
    await onApply(version, installSystem);
    setActing(false);
  };

  return (
    <div className="space-y-3">
      <div className="border border-border rounded-lg p-3 bg-bg-hover/30 text-xs space-y-1">
        <div className="flex items-center gap-1.5 text-text">
          <Info className="w-3.5 h-3.5 shrink-0" />
          {t('vLLM ships its own CUDA runtime and works for most models, but some models need the system CUDA Toolkit (nvcc) to compile kernels at startup:')}
        </div>
        <div className="pl-5 text-text-muted">
          {t('1. System CUDA Toolkit (recommended — most robust; covers the runtime-compiled scenarios)')}
        </div>
        <div className="pl-5 text-text-muted">
          {t('2. Built-in CUDA Runtime only (lightweight; some models fail to start without nvcc)')}
        </div>
      </div>

      {info.current_toolkit && (
        <div className="text-xs text-text-muted">
          {t('Currently installed system toolkit: CUDA {version}', { version: info.current_toolkit })}
        </div>
      )}

      <div className="border border-border rounded-lg overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-bg-hover text-text-muted text-left">
              <th className="px-3 py-2 w-8"></th>
              <th className="px-3 py-2">{t('CUDA version')}</th>
              <th className="px-3 py-2">{t('Minimum driver')}</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {info.versions.map((v) => {
              const req = info.min_driver[v];
              const installedBad = req != null && info.driver_major != null && info.driver_major < req;
              const selectedBad = selDiffers && req != null && selMajor != null && selMajor < req;
              return (
                <tr
                  key={v}
                  className={`border-t border-border cursor-pointer ${pickedVersion === v ? 'bg-bg-hover/60' : 'hover:bg-bg-hover/30'}`}
                  onClick={() => { setCustomVersion(''); onPickVersion(v); }}
                >
                  <td className="px-3 py-2">
                    <input
                      type="radio"
                      name="cuda-pick"
                      checked={pickedVersion === v}
                      onChange={() => onPickVersion(v)}
                      onClick={(e) => e.stopPropagation()}
                      className="accent-sky-400"
                      aria-label={`CUDA ${v}`}
                    />
                  </td>
                  <td className="px-3 py-2 font-mono">CUDA {v}</td>
                  <td className="px-3 py-2">{t('driver ≥ {ver}', { ver: req ?? '—' })}</td>
                  <td className="px-3 py-2 space-y-0.5">
                    {installedBad && (
                      <span className="flex items-center gap-1 text-danger text-[11px]">
                        <XCircle className="w-3 h-3" />
                        {t('installed driver {major} does not support', { major: info.driver_major ?? '—' })}
                      </span>
                    )}
                    {selectedBad && (
                      <span className="flex items-center gap-1 text-warning text-[11px]">
                        <XCircle className="w-3 h-3" />
                        {t('selected driver {major} does not support', { major: selMajor ?? '—' })}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {((info.driver_major != null && info.driver_max_cuda) ||
          (selDiffers && info.selected_driver_max_cuda)) && (
          <div className="border-t border-border px-3 py-1.5 text-[11px] text-text-muted space-y-0.5">
            {info.driver_major != null && info.driver_max_cuda && (
              <div>{t('Installed driver {major} supports up to CUDA {max}', { major: info.driver_major, max: info.driver_max_cuda })}</div>
            )}
            {selDiffers && info.selected_driver_max_cuda && (
              <div>{t('Selected driver {major} supports up to CUDA {max}', { major: selMajor ?? '—', max: info.selected_driver_max_cuda })}</div>
            )}
          </div>
        )}
      </div>

      <button
        onClick={() => setShowAdvanced((s) => !s)}
        className="flex items-center gap-1 text-xs text-text-muted hover:text-text transition-colors"
      >
        <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showAdvanced ? 'rotate-180' : ''}`} />
        {t('Advanced options')}
      </button>
      {showAdvanced && (
        <div>
          <label className="block text-xs text-text-muted mb-1" htmlFor="cuda-custom">
            {t('Custom CUDA version (for testing)')}
          </label>
          <input
            id="cuda-custom"
            type="text"
            value={customVersion}
            onChange={(e) => setCustomVersion(e.target.value.replace(/[^0-9.]/g, ''))}
            placeholder="12.4"
            className="w-full px-3 py-1.5 bg-bg rounded-lg border border-border text-text focus:border-accent focus:outline-none font-mono text-sm"
          />
        </div>
      )}

      {incompatible && version && (
        <div className="flex items-center gap-1.5 text-xs text-danger">
          <XCircle className="w-3.5 h-3.5 shrink-0" />
          {t('Current driver {major} cannot use CUDA {ver} — go back to the GPU Driver tab and upgrade the driver first.', {
            major: info.driver_major ?? '—', ver: version,
          })}
        </div>
      )}

      <div className="space-y-1.5 border border-border rounded-lg p-3">
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input
            type="radio"
            name="cuda-mode"
            checked={installSystem}
            onChange={() => onInstallSystemChange(true)}
            className="accent-sky-400"
          />
          <span>
            {t('Install system CUDA Toolkit (recommended — most robust)')}
            <span className="block text-text-muted pl-4">
              {t('apt install from the NVIDIA repo + PATH / LD_LIBRARY_PATH environment (persisted)')}
            </span>
          </span>
        </label>
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input
            type="radio"
            name="cuda-mode"
            checked={!installSystem}
            onChange={() => onInstallSystemChange(false)}
            className="accent-sky-400"
          />
          <span>
            {t('Skip system CUDA (may fail without nvcc)')}
            <span className="block text-text-muted pl-4">
              {t('No system CUDA is installed; the vLLM tab interlocks with the toolkit already on the server.')}
            </span>
          </span>
        </label>
      </div>

      <div className="flex items-center justify-between pt-1">
        <div className="text-xs text-text-muted">
          {version ? t('Selected: CUDA {ver}', { ver: version }) : ''}
        </div>
        <Button
          variant="primary"
          onClick={handleApply}
          disabled={!version || incompatible || busy || acting}
        >
          {installSystem ? t('Install CUDA Toolkit') : t('Confirm selection')}
        </Button>
      </div>
    </div>
  );
}
