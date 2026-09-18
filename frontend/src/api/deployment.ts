import api from './client';

// ---- types -----------------------------------------------------------------

export interface DriverTag {
  key: string;
  label: string;
  color: string;
}

export interface DriverRow {
  package: string;
  version: number | null;
  attrs: string[];
  tags: DriverTag[];
  recommended: boolean;
  nouveau: boolean;
  installed?: boolean;
  note?: string;
}

export interface DriverList {
  gpu_models: string[];
  drivers: DriverRow[];
  current_driver: string;
  current_major: number | null;
  min_driver: number;
  driver_min_cuda: Record<string, string>;
  pending_task: { id: string; package: string; status: string } | null;
}

export interface PreflightItem {
  key: string;
  label: string;
  status: 'ok' | 'warn' | 'fail';
  value: string;
  detail: string;
}

export interface Preflight {
  ok: boolean;
  blocking: boolean;
  items: PreflightItem[];
  checked_at: string;
}

export interface CudaInfo {
  versions: string[];
  min_driver: Record<string, number>;
  selected: string;
  install_system: boolean;
  driver_major: number | null;
  current_toolkit: string;
  installed_toolkit: string;
}

export interface VllmInfo {
  uv: string;
  uv_missing: boolean;
  pythons: string[];
  venvs: string[];
  vllm_version: string;
  venv_name: string;
  cuda_versions: string[];
  selected: { version: string; runtime: string; cuda: string };
  snapshot: { version: string; previous_version: string; venv: string };
}

export interface DeployTask {
  id: string;
  kind: string;
  title: string;
  status: 'queued' | 'running' | 'success' | 'failed' | 'cancelled' | 'awaiting_reboot' | 'interrupted';
  steps: { name: string; status: string }[];
  current_step: number;
  error: string;
  suggestion: string;
  result: Record<string, unknown>;
  created_at: string;
  started_at: string;
  finished_at: string;
  log_tail?: string[];
}

export interface DeployState {
  mirrors: { pypi: string; hf: string };
  selected: {
    driver: string;
    cuda: string;
    cuda_install_system: boolean;
    vllm_version: string;
    vllm_runtime: string;
  };
  snapshots: {
    driver: { current_pkg: string; previous_pkg: string; previous_version: string };
    cuda: { installed_version: string; toolkit_path: string };
    vllm: { version: string; previous_version: string; venv: string };
  };
  history: { ts: string; kind: string; title: string; status: string }[];
  current_driver_major: number | null;
  pending_driver_task: { id: string; package: string; status: string } | null;
  locks: { cuda: boolean; vllm: boolean };
}

export interface ConflictPackage {
  state: string;
  name: string;
  version: string;
  suggestion: string;
}

export interface DeployTemplate {
  id: string;
  name: string;
  driver: string;
  cuda: string;
  cuda_install_system: boolean;
  vllm_version: string;
  vllm_runtime: string;
}

// ---- endpoints -------------------------------------------------------------

export const getPreflight = async (): Promise<Preflight> => {
  const response = await api.get('/deployment/preflight', { timeout: 90000 });
  return response.data;
};

export const getDrivers = async (): Promise<DriverList> => {
  const response = await api.get('/deployment/drivers', { timeout: 60000 });
  return response.data;
};

export const applyDriver = async (pkg: string): Promise<{ status: string; task_id: string }> => {
  const response = await api.post('/deployment/drivers/apply', { package: pkg });
  return response.data;
};

export const getCudaInfo = async (): Promise<CudaInfo> => {
  const response = await api.get('/deployment/cuda', { timeout: 60000 });
  return response.data;
};

export const applyCuda = async (version: string, installSystem: boolean): Promise<{ status: string; task_id: string | null; message?: string }> => {
  const response = await api.post('/deployment/cuda/apply', { version, install_system: installSystem });
  return response.data;
};

export const getVllmInfo = async (): Promise<VllmInfo> => {
  const response = await api.get('/deployment/vllm', { timeout: 60000 });
  return response.data;
};

export interface VllmApplyPayload {
  version: string;
  runtime_mode: 'builtin' | 'system';
  env_mode: 'new' | 'existing';
  venv_name: string;
  python_version: string;
  source_build: boolean;
  flashinfer: boolean;
  nccl: boolean;
  auto_register_service: boolean;
  cuda_version?: string;
}

export const applyVllm = async (payload: VllmApplyPayload): Promise<{ status: string; task_id: string }> => {
  const response = await api.post('/deployment/vllm/apply', payload);
  return response.data;
};

export const getTasks = async (limit = 30): Promise<DeployTask[]> => {
  const response = await api.get(`/deployment/tasks?limit=${limit}`);
  return response.data.tasks;
};

export const getTask = async (id: string): Promise<DeployTask> => {
  const response = await api.get(`/deployment/tasks/${id}`);
  return response.data;
};

export const getTaskLog = async (id: string, offset = 0): Promise<{ content: string; offset: number }> => {
  const response = await api.get(`/deployment/tasks/${id}/log?offset=${offset}`);
  return response.data;
};

export const cancelTask = async (id: string): Promise<void> => {
  await api.post(`/deployment/tasks/${id}/cancel`);
};

export const getDeployState = async (): Promise<DeployState> => {
  const response = await api.get('/deployment/state', { timeout: 60000 });
  return response.data;
};

export const updateDeployState = async (payload: {
  mirrors?: { pypi?: string; hf?: string };
  selected?: Partial<DeployState['selected']>;
}): Promise<DeployState> => {
  const response = await api.put('/deployment/state', payload);
  return response.data;
};

export const rebootServer = async (): Promise<{ status: string; task_id: string }> => {
  const response = await api.post('/deployment/reboot');
  return response.data;
};

export const rollbackEnv = async (target: 'driver' | 'vllm'): Promise<{ status: string; task_id: string }> => {
  const response = await api.post('/deployment/rollback', { target });
  return response.data;
};

export const scanConflicts = async (): Promise<ConflictPackage[]> => {
  const response = await api.post('/deployment/conflict-scan', null, { timeout: 60000 });
  return response.data.packages;
};

export const cleanupConflicts = async (packages: string[]): Promise<{ status: string; task_id: string }> => {
  const response = await api.post('/deployment/conflict-cleanup', { packages });
  return response.data;
};

export const getTemplates = async (): Promise<DeployTemplate[]> => {
  const response = await api.get('/deployment/templates');
  return response.data.templates;
};

export const exportEnv = async (): Promise<{ yaml: string; filename: string }> => {
  const response = await api.get('/deployment/export', { timeout: 90000 });
  return response.data;
};

export const importEnv = async (yaml: string): Promise<DeployState> => {
  const response = await api.post('/deployment/import', { yaml });
  return response.data.state;
};

// ---- helpers -----------------------------------------------------------------

export const API_ERROR_PREFIX = 'Request failed with status';

export function apiErrorMessage(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = e?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string };
    if (typeof first?.msg === 'string') return first.msg;
  }
  if (typeof e?.message === 'string' && e.message && !e.message.startsWith(API_ERROR_PREFIX)) return e.message;
  return 'Request failed';
}
