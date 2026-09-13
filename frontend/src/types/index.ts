export interface GPUInfo {
  index: number;
  name: string;
  memory_total_mb: number;
  memory_used_mb: number;
  temperature_c: number;
  power_draw_w: number;
  power_limit_w: number;
  utilization_gpu: number;
  utilization_memory: number;
}

export interface CPUInfo {
  model: string;
  count: number;
  usage: number;
}

export interface MemoryInfo {
  total: number;
  used: number;
  free: number;
  usage: number;
}

export interface DiskInfo {
  total: number;
  used: number;
  free: number;
  usage: number;
}

export interface HardwareMetrics {
  gpus: GPUInfo[];
  cpu: CPUInfo;
  memory: MemoryInfo;
  disk: DiskInfo;
}

export interface SoftwareInfo {
  os: string;
  gpu_driver: string;
  cuda_toolkit: string;
  vllm: string;
}

export interface ServerConfig {
  id: string;
  host: string;
  port: number;
  api_key?: string;
  use_auth: boolean;
  ssh_port: number;
  ssh_username: string;
  ssh_password?: string;
  ssh_key_path?: string;
  venv_name: string;
  model_save_path?: string;
  bmc_host: string;
  bmc_username: string;
  bmc_password?: string;
}

export interface ServerHealth {
  status: string;
  version?: string;
  mode?: string;
  uptime_seconds?: number;
}

export interface SshStatus {
  connected: boolean;
  error?: string;
  host_down?: boolean;
}

export interface BmcStatus {
  configured: boolean;
  connected: boolean;
  power: 'on' | 'off' | null;
  error?: string;
}

export interface CliStatus {
  success: boolean;
  hf_installed: boolean;
}

export interface InstallCliResponse {
  status: string;
  pid: number | null;
  log_file: string;
}

export type InstallStatus = 'installing' | 'complete' | 'failed' | 'not_found';

export interface InstallStatusResponse {
  status: InstallStatus;
  message: string;
  log?: string;
}

export const ModelStatus = {
  DOWNLOADED: 'downloaded',
  LOADING: 'loading',
  RUNNING: 'running',
  STOPPED: 'stopped',
  FAILED: 'failed',
} as const;

export type ModelStatusValue = typeof ModelStatus[keyof typeof ModelStatus];

export interface ModelInfo {
  id: string;
  name: string;
  status: ModelStatusValue;
  path: string;
  size_bytes: number;
  config?: { name: string; path: string };
  created_at?: string;
  updated_at?: string;
  error_message?: string;
}

export interface BenchmarkResult {
  tokens_per_second: number;
  prompt_tokens: number;
  completion_tokens: number;
  elapsed_seconds: number;
  timestamp: number;
}

export interface ModelRuntimeStatus {
  status: 'connected' | 'disconnected';
  models: string[];
  running_requests: number | null;
  waiting_requests: number | null;
  kv_cache_usage_pct: number | null;
  generation_tokens_per_s: number | null;
  prompt_tokens_per_s: number | null;
  mtp_hit_rate_pct: number | null;
  prefix_cache_hit_rate_pct: number | null;
  mtp_hit_rate_cumulative_pct: number | null;
  prefix_cache_hit_rate_cumulative_pct: number | null;
  avg_ttft_s: number | null;
  avg_tpot_ms: number | null;
  avg_e2e_latency_s: number | null;
  preemptions_total: number | null;
  timestamp: number;
}

export interface StopResult {
  status: string;
  stopped: boolean;
  error?: string;
}

export interface LaunchConfig {
  model_id: string;
  start_command: string;
  env_vars?: string;
}

export interface LaunchConfigResponse {
  has_config: boolean;
  config: LaunchConfig | null;
}

export interface AuthStore {
  authRequired: boolean;
  lastPromptAt: number;
  requestAuth: () => void;
}

export interface ModelStore {
  models: ModelInfo[];
  loading: boolean;
  error: string | null;
  fetchModels: () => Promise<void>;
  syncModelStatus: () => Promise<{ synced: boolean; synced_count?: number; error?: string }>;
  stopModel: (modelId: string) => Promise<StopResult>;
  startModel: (modelId: string) => Promise<{ status: string; model_id: string; command: string }>;
  clearError: () => void;
  scanModels: () => Promise<{ count: number; error?: string; removed_stale?: string[] }>;
  getLaunchConfig: (modelId: string) => Promise<LaunchConfigResponse>;
  saveLaunchConfig: (modelId: string, config: { start_command: string; env_vars: string }) => Promise<void>;
  deleteModel: (modelId: string) => Promise<void>;
}

export interface HardwareStore {
  gpus: GPUInfo[];
  cpu: CPUInfo | null;
  memory: MemoryInfo | null;
  disk: DiskInfo | null;
  software: SoftwareInfo | null;
  addMetrics: (payload: MetricsPayload) => void;
  fetchHardwareMetrics: () => Promise<void>;
  fetchSoftware: () => Promise<void>;
}

export interface MetricsPayload {
  gpus: GPUInfo[];
  cpu: CPUInfo | null;
  memory: MemoryInfo | null;
  disk: DiskInfo | null;
  model?: ModelRuntimeStatus | null;
  timestamp: number;
  error?: string;
}
