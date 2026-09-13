export const ZH: Record<string, string> = {
  'Connected': '已连接',
  'Disconnected': '已断开',
  'Server Config': '服务器配置',
  'Power OFF': '关闭电源',
  'Power ON': '打开电源',
  'Switch to light theme': '切换到浅色主题',
  'Switch to dark theme': '切换到深色主题',
  'Downloading {name}...': '正在下载 {name}...',
  'Close': '关闭',

  'RAM': '内存',
  'DISK': '磁盘',
  'Memory': '显存',
  'Power': '功耗',
  'Temp': '温度',
  'GPU Driver': '显卡驱动',
  'OS': '操作系统',
  'CPU': '处理器',
  'GPU': '显卡',
  'Core': '核心',

  'Model Status': '模型状态',
  'Loading…': '加载中…',
  'CUDA': 'CUDA',
  'vLLM': 'vLLM',
  'Running/Queue · Preemptions': '运行/排队 · 抢占',
  'KV Cache': 'KV 缓存',
  'Prompt Throughput (tok/s)': '提示吞吐（tok/s）',
  'Generation Throughput (tok/s)': '生成吞吐（tok/s）',
  'MTP Hit Rate (Realtime/Cumulative)': 'MTP 命中率（实时/累计）',
  'Prefix Cache Hit Rate (Realtime/Cumulative)': '前缀缓存命中率（实时/累计）',
  'Avg TTFT (s)': '平均首字延迟（s）',
  'Avg TPOT (ms)': '平均单字延迟（ms）',
  'Avg E2E Latency (s)': '平均端到端延迟（s）',

  'Models': '模型',
  'Model Download': '模型下载',
  'Model Download (SSH disconnected)': '模型下载（SSH 未连接）',
  'Benchmark': '测速',
  'Benchmark (SSH disconnected)': '测速（SSH 未连接）',
  'Refresh': '刷新',
  'Refresh (SSH disconnected)': '刷新（SSH 未连接）',
  'Sync failed: {error}': '同步失败：{error}',
  'Network error': '网络错误',
  'Request timeout': '请求超时',
  'Remote host not configured': '未配置目标服务器',
  'Failed to connect to SSH': 'SSH 连接失败',
  'Model save path must be under home directory': '模型保存路径必须位于远端 HOME 目录下',
  'Path traversal is not allowed': '路径不允许使用 .. 跨越目录',
  'Path traversal not allowed': '路径不允许使用 .. 跨越目录',
  'Start command not configured': '未配置启动命令',
  'Console only available in remote mode': 'Console 仅在远程模式可用',
  'Waiting for SSH connection...': '等待 SSH 连接...',
  'No models found. Click refresh to scan.': '未找到模型，点击刷新扫描。',
  'Benchmark tok/s (single 128-token run, server-side token count)':
    '测速 tok/s（单次 128 token 生成，服务端 token 计数）',
  'No record': '无记录',
  'Start': '启动',
  'Start (SSH disconnected)': '启动（SSH 未连接）',
  'Cancel': '取消',
  'Stop': '停止',
  'Launch Config': '启动配置',
  'Delete': '删除',
  'Delete (SSH disconnected)': '删除（SSH 未连接）',

  'IP Address': 'IP 地址',
  'Server Address (IPv4)': '服务器地址（IPv4）',
  'BMC Address (IPv4)': 'BMC 地址（IPv4）',
  'Optional': '可选',
  'SSH Username': 'SSH 用户名',
  'SSH Password': 'SSH 密码',
  'activated before download/run: source ~/{venv}/bin/activate':
    '下载/运行前激活：source ~/{venv}/bin/activate',
  'vLLM Venv Name': 'vLLM 虚拟环境名',
  'SSH Port': 'SSH 端口',
  'SSH Key Path': 'SSH 密钥路径',
  'API Port': 'API 端口',
  'BMC Username': 'BMC 用户名',
  'BMC Password': 'BMC 密码',
  'Save': '保存',

  'API Key Required': '需要 API 密钥',
  'The server requires an API key to access. Enter the API key to continue.':
    '服务器已启用 API 密钥鉴权，请输入密钥以继续。',
  'API Key': 'API 密钥',
  'Confirm': '确认',
  'Invalid API key': 'API 密钥无效',
  'Verification failed': '验证失败',

  'Launch Config: {name}': '启动配置：{name}',
  'Model Path': '模型路径',
  'Environment Variables': '环境变量',
  '# KEY=VALUE per line (export prefix ok), applied before the start command\nHF_ENDPOINT=https://hf-mirror.com':
    '# 每行 KEY=VALUE（export 前缀可选），先于启动命令生效\nHF_ENDPOINT=https://hf-mirror.com',
  'Start Command': '启动命令',

  'Download Model': '下载模型',
  'Checking CLI tools...': '正在检查 CLI 工具...',
  'hf is installed': 'hf 已安装',
  'hf (huggingface_hub) not found': 'hf (huggingface_hub) 未安装',
  'Installing...': '安装中...',
  'Install': '安装',
  'Model Repo (e.g., Qwen/Qwen2-7B)': '模型仓库（如 Qwen/Qwen2-7B）',
  'Model Save Path': '模型保存路径',
  "Must be under the remote user's HOME directory (~ is supported). Shell metacharacters are not allowed.":
    '必须位于远端用户 HOME 目录下（支持 ~ 前缀），不允许 shell 元字符。',
  'Default when left empty: ~/.cache/huggingface/hub/':
    '留空时默认：~/.cache/huggingface/hub/',
  'HF Mirror': 'HF 镜像',
  'Domestic (hf-mirror.com)': '国内镜像（hf-mirror.com）',
  'Official (huggingface.co)': '官方（huggingface.co）',
  'Download': '下载',

  'Token Benchmark': 'Token 测速',
  'No running model': '没有运行中的模型',
  'Start a model before benchmarking': '测速前请先启动模型',
  'Current Model': '当前模型',
  'Benchmarking…': '测速中…',
  'Start Benchmark': '开始测速',

  'Scan failed: {error}': '扫描失败：{error}',
  'Scanned {count} models': '已扫描 {count} 个模型',
  'Removed {count} stale model(s)': '移除 {count} 个失效模型',
  'Updated {count} statuses': '已更新 {count} 个状态',
  'Sync failed': '同步失败',
  'Refreshed': '已刷新',
  'Refresh failed': '刷新失败',
  'Delete {name}?': '删除 {name}？',
  'The model files on the server will also be deleted.': '服务器上的模型文件也将被删除。',
  'Stop {name}?': '停止 {name}？',
  'Ongoing inference requests will be interrupted.': '正在进行的推理请求将被中断。',
  'Operation failed': '操作失败',
  "Start command not configured — open the model's Launch Config and set a start command first.":
    '启动命令未配置 — 请先打开该模型的启动配置并设置启动命令。',
  'Start failed: {msg}': '启动失败：{msg}',
  'unknown error': '未知错误',
  'Stop timed out: the process may still be running. GPU memory may remain in use.':
    '停止超时：进程可能仍在运行，显存可能仍被占用。',

  'Installing huggingface_hub...': '正在安装 huggingface_hub...',
  'Download status polling stopped after 1 hour — the download may still be running in the background. Check the model list or backend logs for its real status.':
    '下载状态轮询已停止（1 小时上限）——下载可能仍在后台进行，请查看模型列表或后端日志确认实际状态。',
  'CLI install status polling stopped after 1 hour — the installation may still be running in the background. Check the CLI tools status or backend logs for its real status.':
    'CLI 安装状态轮询已停止（1 小时上限）——安装可能仍在后台进行，请查看 CLI 工具状态或后端日志确认实际状态。',
  'Download request failed': '下载请求失败',
  'Download failed': '下载失败',

  'Config load failed — reload the page before saving': '配置加载失败 — 保存前请刷新页面',
  'Configuration saved': '配置已保存',
  'Save failed': '保存失败',
  'Shut down the AI server?': '确定关闭 AI 服务器？',
  'The machine will power off and vLLM will be stopped. You will need to power it back on to use it again.':
    '机器将断电，vLLM 停止，需重新上电后才能再次使用。',
  'Server is powered off': '服务器已关机',
  'Server is starting up': '服务器正在启动',
  'Server SSH not connected': '服务器SSH未连接',

  'Console': '控制台',
  '[WebSocket] Reconnect limit reached. Check the AI server and network.':
    '[WebSocket] 重连次数已达上限，请检查 AI 服务器与网络。',
  '✗ Connection error': '✗ 连接错误',
};

export const LANG_KEY = 'vllm_lang';

export function readStoredLang(): 'en' | 'zh' {
  try {
    return localStorage.getItem(LANG_KEY) === 'zh' ? 'zh' : 'en';
  } catch {
    return 'en';
  }
}

export function tRaw(key: string): string {
  return readStoredLang() === 'zh' ? ZH[key] ?? key : key;
}

const BACKEND_ERROR_KEYS: Record<string, string> = {
  'Remote host not configured': 'Remote host not configured',
  'Failed to connect to SSH': 'Failed to connect to SSH',
  'Model save path must be under home directory': 'Model save path must be under home directory',
  'Path traversal is not allowed': 'Path traversal is not allowed',
  'Path traversal not allowed': 'Path traversal not allowed',
  'Start command not configured': 'Start command not configured',
  'Console only available in remote mode': 'Console only available in remote mode',
};

export function mapBackendError(msg: string): string {
  const key =
    BACKEND_ERROR_KEYS[msg] ??
    Object.keys(BACKEND_ERROR_KEYS).find((k) => msg.startsWith(k));
  return key ? tRaw(key) : msg;
}
