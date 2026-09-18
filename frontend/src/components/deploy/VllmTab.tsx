import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import type { VllmApplyPayload, VllmInfo } from '../../api/deployment';
import { useI18n } from '../../i18n';
import { Button } from '../ui/Button';

interface VllmTabProps {
  info: VllmInfo | null;
  cudaVersion: string;
  cudaInstallSystem: boolean;
  currentToolkit: string;
  pypiMirror: string;
  onApply: (payload: VllmApplyPayload) => Promise<boolean>;
  busy: boolean;
}

const FIELD_CLASS = 'w-full px-3 py-1.5 bg-bg rounded-lg border border-border text-text focus:border-accent focus:outline-none font-mono text-sm';

export function VllmTab({
  info, cudaVersion, cudaInstallSystem, currentToolkit, pypiMirror, onApply, busy,
}: VllmTabProps) {
  const { t } = useI18n();
  const [version, setVersion] = useState('latest');
  const [envMode, setEnvMode] = useState<'new' | 'existing'>('new');
  const [venvName, setVenvName] = useState('');
  const [pyVersion, setPyVersion] = useState('');
  const [autoRegister, setAutoRegister] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [sourceBuild, setSourceBuild] = useState(false);
  const [acting, setActing] = useState(false);

  if (!info) {
    return <div className="text-xs text-text-muted py-6 text-center">{t('Loading...')}</div>;
  }

  const effVenv = venvName.trim() || info.venv_name || '.vllm';
  const spec = version.trim() && version.trim() !== 'latest' ? `vllm==${version.trim()}` : 'vllm';
  const mirrorFlag = pypiMirror ? ` --index-url ${pypiMirror}` : '';
  // The torch backend auto-binds to the effective CUDA: the selected version
  // when the system install is on (the state it brings the server to); when
  // skipping the install the toolkit already on the server wins (it is what
  // actually runs), with the selection as fallback when no toolkit exists.
  // torch wheels ship per CUDA toolkit version: CUDA 13 has cu130 and cu132
  // (no torch wheels for cu131/cu133/cu134); 13.2 → cu132, the other 13.x
  // → cu130 (vLLM's docs standardize on it, widest torch range).
  const effectiveCuda = cudaInstallSystem
    ? (cudaVersion || currentToolkit)
    : (currentToolkit || cudaVersion);
  const family = (v: string) => (v.startsWith('13') ? '13' : v.startsWith('12') ? '12' : '');
  const effFamily = family(effectiveCuda);
  const selFamily = family(cudaVersion);
  // 13.x forces the system runtime (cu130/cu132); 12.x uses the system
  // runtime when the selection agrees (the backend pins torch to it), else
  // the built-in cu129 wheel (same family).
  const runtimeMode: 'builtin' | 'system' =
    effFamily === '13' ? 'system'
    : effFamily === '12' ? (selFamily === '12' ? 'system' : 'builtin')
    : 'builtin';
  // torch index: 13.x follows the effective CUDA (13.2 → cu132, else cu130);
  // system 12.x follows the selected version (the backend pins torch to it);
  // built-in → cu129 (the PyPI default family, pinned explicitly).
  const torchIndex = effFamily === '13'
    ? (effectiveCuda === '13.2' ? 'cu132' : 'cu130')
    : runtimeMode === 'system' ? `cu${cudaVersion.replace('.', '')}`
    : 'cu129';
  // the system runtime needs a selected version agreeing with the effective
  // family (backend check); skip-system-CUDA mode needs no version
  const systemBlocked = runtimeMode === 'system' && cudaInstallSystem && selFamily !== effFamily;
  // the version the auto-bound index matches: the effective CUDA for 13.x
  // (the index follows it), the selection for 12.x
  const boundVer = effFamily === '13' ? effectiveCuda : cudaVersion;
  const previewCmd = sourceBuild
    ? `git clone --depth 1 <vllm repo> /tmp/vllm-src-vdb\nuv pip install${mirrorFlag} --python ~/${effVenv}/bin/python -e /tmp/vllm-src-vdb`
    : cudaVersion === ''
      ? `uv pip install${mirrorFlag} --python ~/${effVenv}/bin/python ${spec}`
      : `uv pip install${mirrorFlag} --python ~/${effVenv}/bin/python ${spec} torch --extra-index-url https://download.pytorch.org/whl/${torchIndex}`;

  const handleApply = async () => {
    setActing(true);
    await onApply({
      version: version.trim() || 'latest',
      runtime_mode: runtimeMode,
      env_mode: envMode,
      venv_name: effVenv,
      python_version: pyVersion,
      source_build: sourceBuild,
      auto_register_service: autoRegister,
      cuda_version: cudaVersion,
    });
    setActing(false);
  };

  return (
    <div className="space-y-3">
      {info.vllm_version && (
        <div className="text-xs text-text-muted">
          {t('Currently installed: vLLM {version}', { version: info.vllm_version })}
        </div>
      )}

      {/* version */}
      <div className="space-y-1.5 border border-border rounded-lg p-3">
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input type="radio" name="vllm-version" checked={version === 'latest'}
            onChange={() => setVersion('latest')} className="accent-sky-400" />
          <span>{t('Latest stable (PyPI)')}</span>
        </label>
        <label className="flex items-center gap-2 text-xs">
          <input type="radio" name="vllm-version" checked={version !== 'latest'}
            onChange={() => setVersion((v) => (v === 'latest' ? '0.29.0' : v))} className="accent-sky-400" />
          <span className="flex items-center gap-2 flex-1">
            {t('Pinned version')}
            <input
              type="text"
              value={version === 'latest' ? '' : version}
              onChange={(e) => setVersion(e.target.value.replace(/[^0-9.]/g, ''))}
              placeholder="0.29.0"
              className={`${FIELD_CLASS} !w-40`}
              disabled={version === 'latest'}
            />
          </span>
        </label>
      </div>

      {/* CUDA runtime — auto-bound to the effective CUDA, not selectable */}
      <div className="space-y-1.5 border border-border rounded-lg p-3">
        <div className="text-xs font-medium">{t('CUDA Runtime binding')}</div>
        {!cudaInstallSystem ? (
          <div className="text-xs">{t('Auto-bound: built-in CUDA runtime, PyPI default torch backend')}</div>
        ) : systemBlocked ? (
          <div className="text-xs text-warning">{t('No CUDA version selected')}</div>
        ) : cudaVersion === '' ? (
          <div className="text-xs">{t('No CUDA version selected')}</div>
        ) : (
          <div className="text-xs">
            {runtimeMode === 'system'
              ? t('Auto-bound: system CUDA runtime, {index} torch backend (matches CUDA {ver})', { index: torchIndex, ver: boundVer })
              : t('Auto-bound: built-in CUDA runtime, {index} torch backend (matches CUDA {ver})', { index: torchIndex, ver: effectiveCuda })}
          </div>
        )}
        {!systemBlocked && cudaVersion !== '' && effFamily !== '' &&
          ((effFamily === '13' && cudaVersion !== effectiveCuda) ||
           (selFamily === '13' && effFamily === '12')) && (
          <div className="text-xs text-warning">
            {t('Selected CUDA {sel} conflicts with the system CUDA {cur} — the binding follows the system toolkit.', { sel: cudaVersion, cur: effectiveCuda })}
          </div>
        )}
        <pre className="text-[11px] font-mono bg-bg rounded-lg border border-border p-2 overflow-x-auto whitespace-pre-wrap break-all text-text-muted">
          {previewCmd}
        </pre>
      </div>

      {/* python environment */}
      <div className="space-y-2 border border-border rounded-lg p-3">
        <div className="text-xs font-medium">{t('Python environment')}</div>
        {info.pythons.length > 0 && (
          <div>
            <label className="block text-xs text-text-muted mb-1" htmlFor="vllm-py">
              {t('Python interpreter (uv managed)')}
            </label>
            <select
              id="vllm-py"
              value={pyVersion}
              onChange={(e) => setPyVersion(e.target.value)}
              className={`${FIELD_CLASS} !font-sans`}
            >
              <option value="">{t('System default')}</option>
              {info.pythons.map((p) => <option key={p} value={p}>Python {p}</option>)}
            </select>
          </div>
        )}
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-1.5 text-xs cursor-pointer">
            <input type="radio" name="vllm-env" checked={envMode === 'new'}
              onChange={() => setEnvMode('new')} className="accent-sky-400" />
            {t('New virtualenv')}
          </label>
          <label className="flex items-center gap-1.5 text-xs cursor-pointer">
            <input type="radio" name="vllm-env" checked={envMode === 'existing'}
              onChange={() => setEnvMode('existing')} className="accent-sky-400" />
            {t('Existing virtualenv')}
          </label>
        </div>
        {envMode === 'new' ? (
          <div className="flex items-center gap-2">
            <label className="text-xs text-text-muted shrink-0" htmlFor="vllm-venv">
              {t('Venv name')}
            </label>
            <input
              id="vllm-venv"
              type="text"
              value={venvName}
              onChange={(e) => setVenvName(e.target.value)}
              placeholder={info.venv_name || '.vllm'}
              className={FIELD_CLASS}
            />
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <label className="text-xs text-text-muted shrink-0" htmlFor="vllm-venv-pick">
              {t('Existing virtualenv')}
            </label>
            <select
              id="vllm-venv-pick"
              value={info.venvs.includes(venvName) ? venvName : ''}
              onChange={(e) => setVenvName(e.target.value)}
              className={`${FIELD_CLASS} !font-sans`}
            >
              <option value="">{t('Select…')}</option>
              {info.venvs.map((v) => <option key={v} value={v}>{v.replace(/^\/[^/]*\//, '~/')}</option>)}
            </select>
          </div>
        )}
      </div>

      {/* advanced */}
      <button
        onClick={() => setShowAdvanced((s) => !s)}
        className="flex items-center gap-1 text-xs text-text-muted hover:text-text transition-colors"
      >
        <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showAdvanced ? 'rotate-180' : ''}`} />
        {t('Advanced options')}
      </button>
      {showAdvanced && (
        <div className="space-y-1.5 border border-border rounded-lg p-3">
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={sourceBuild} onChange={(e) => setSourceBuild(e.target.checked)} className="accent-sky-400" />
            {t('Local source build (development)')}
            <span className="text-text-muted">{t('git clone + editable install instead of the PyPI wheel')}</span>
          </label>
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={autoRegister} onChange={(e) => setAutoRegister(e.target.checked)} className="accent-sky-400" />
            {t('Register vLLM under supervisor after install (template, autostart off)')}
          </label>
        </div>
      )}

      <div className="flex items-center justify-between pt-1">
        <div className="text-xs text-text-muted">
          {info.uv_missing ? t('uv not detected on the server') : t('uv {version} detected', { version: info.uv })}
        </div>
        <Button
          variant="primary"
          onClick={handleApply}
          disabled={busy || acting || systemBlocked || (version !== 'latest' && !/^\d+\.\d+\.\d+$/.test(version.trim())) || (envMode === 'new' && pyVersion !== '' && !/^\d+\.\d{1,2}$/.test(pyVersion))}
        >
          {t('Install vLLM')}
        </Button>
      </div>
    </div>
  );
}
