import { useState } from 'react';
import { ChevronDown, Lock } from 'lucide-react';
import type { VllmApplyPayload, VllmInfo } from '../../api/deployment';
import { useI18n } from '../../i18n';
import { Button } from '../ui/Button';

interface VllmTabProps {
  locked: boolean;
  info: VllmInfo | null;
  cudaVersion: string;
  cudaInstallSystem: boolean;
  pypiMirror: string;
  onApply: (payload: VllmApplyPayload) => Promise<boolean>;
  busy: boolean;
}

const FIELD_CLASS = 'w-full px-3 py-1.5 bg-bg rounded-lg border border-border text-text focus:border-accent focus:outline-none font-mono text-sm';

export function VllmTab({
  locked, info, cudaVersion, cudaInstallSystem, pypiMirror, onApply, busy,
}: VllmTabProps) {
  const { t } = useI18n();
  const [version, setVersion] = useState('latest');
  const [runtime, setRuntime] = useState<'builtin' | 'system'>('builtin');
  const [envMode, setEnvMode] = useState<'new' | 'existing'>('new');
  const [venvName, setVenvName] = useState('');
  const [pyVersion, setPyVersion] = useState('');
  const [flashinfer, setFlashinfer] = useState(false);
  const [mtp, setMtp] = useState(false);
  const [nccl, setNccl] = useState(false);
  const [rustFrontend, setRustFrontend] = useState(false);
  const [autoRegister, setAutoRegister] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [sourceBuild, setSourceBuild] = useState(false);
  const [acting, setActing] = useState(false);

  if (locked) {
    return (
      <div className="border border-border rounded-lg p-8 text-center">
        <Lock className="w-6 h-6 text-text-muted mx-auto mb-2" />
        <div className="text-sm text-text-muted">{t('Locked: select a CUDA version in the CUDA Toolkit tab first.')}</div>
      </div>
    );
  }

  if (!info) {
    return <div className="text-xs text-text-muted py-6 text-center">{t('Loading...')}</div>;
  }

  const effVenv = venvName.trim() || info.venv_name || '.vllm';
  const spec = version.trim() && version.trim() !== 'latest' ? `vllm==${version.trim()}` : 'vllm';
  const mirrorFlag = pypiMirror ? ` --index-url ${pypiMirror}` : '';
  const effRuntime: 'builtin' | 'system' =
    runtime === 'system' && !cudaInstallSystem ? 'builtin' : runtime;
  const previewCmd = sourceBuild
    ? `git clone --depth 1 <vllm repo> /tmp/vllm-src-vdb\nuv pip install${mirrorFlag} --python ~/${effVenv}/bin/python -e /tmp/vllm-src-vdb`
    : effRuntime === 'system'
      ? `uv pip install${mirrorFlag} --python ~/${effVenv}/bin/python ${spec} torch --extra-index-url https://download.pytorch.org/whl/cu${(cudaVersion || '12.9').replace('.', '')}`
      : `uv pip install${mirrorFlag} --python ~/${effVenv}/bin/python ${spec}`;

  const handleApply = async () => {
    setActing(true);
    await onApply({
      version: version.trim() || 'latest',
      runtime_mode: effRuntime,
      env_mode: envMode,
      venv_name: effVenv,
      python_version: pyVersion,
      source_build: sourceBuild,
      flashinfer,
      mtp,
      nccl,
      rust_frontend: rustFrontend,
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

      {/* CUDA runtime binding */}
      <div className="space-y-1.5 border border-border rounded-lg p-3">
        <div className="text-xs font-medium">{t('CUDA Runtime binding')}</div>
        <label className={`flex items-start gap-2 text-xs ${cudaInstallSystem ? 'cursor-pointer' : 'opacity-60'}`}>
          <input
            type="radio"
            name="vllm-runtime"
            checked={effRuntime === 'builtin'}
            onChange={() => setRuntime('builtin')}
            className="accent-sky-400 mt-0.5"
          />
          <span>
            <span className="text-success">{t('Recommended')}</span> · {t('vLLM built-in CUDA Runtime')}
            <span className="block text-text-muted pl-4">
              {t('No dependency on the system CUDA Toolkit — isolation stays clean even without a system CUDA install.')}
            </span>
          </span>
        </label>
        <label className={`flex items-start gap-2 text-xs ${cudaInstallSystem ? 'cursor-pointer' : 'cursor-not-allowed'}`}>
          <input
            type="radio"
            name="vllm-runtime"
            checked={effRuntime === 'system'}
            onChange={() => cudaInstallSystem && setRuntime('system')}
            disabled={!cudaInstallSystem}
            className="accent-sky-400 mt-0.5"
          />
          <span>
            {t('Use the local system CUDA Runtime')}
            <span className="block text-text-muted pl-4">
              {cudaInstallSystem
                ? t('Installs the matching CUDA {ver} torch backend alongside vLLM.', { ver: cudaVersion })
                : t('Not selectable — install the system CUDA Toolkit in the CUDA tab first.')}
            </span>
          </span>
        </label>
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
          {info.venvs.length > 0 && (
            <select
              value=""
              onChange={(e) => { if (e.target.value) { setEnvMode('existing'); setVenvName(e.target.value); } }}
              className={`${FIELD_CLASS} !font-sans max-w-48`}
              aria-label={t('Existing virtualenvs')}
            >
              <option value="">{t('…or pick existing')}</option>
              {info.venvs.map((v) => <option key={v} value={v}>{v.replace(/^\/[^/]*\//, '~/')}</option>)}
            </select>
          )}
        </div>
      </div>

      {/* optional dependencies */}
      <div className="space-y-1.5 border border-border rounded-lg p-3">
        <div className="text-xs font-medium">{t('Optional dependencies')}</div>
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input type="checkbox" checked={flashinfer} onChange={(e) => setFlashinfer(e.target.checked)} className="accent-sky-400" />
          FlashInfer
        </label>
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input type="checkbox" checked={mtp} onChange={(e) => setMtp(e.target.checked)} className="accent-sky-400" />
          {t('MTP speculative decoding dependencies')}
        </label>
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input type="checkbox" checked={nccl} onChange={(e) => setNccl(e.target.checked)} className="accent-sky-400" />
          NCCL ({t('cluster multi-GPU communication')})
        </label>
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input type="checkbox" checked={rustFrontend} onChange={(e) => setRustFrontend(e.target.checked)} className="accent-sky-400" />
          {t('Rust Frontend')}
        </label>
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
          disabled={busy || acting || (version !== 'latest' && !/^\d+\.\d+\.\d+$/.test(version.trim())) || (envMode === 'new' && pyVersion !== '' && !/^\d+\.\d{1,2}$/.test(pyVersion))}
        >
          {t('Install vLLM')}
        </Button>
      </div>
    </div>
  );
}
