export const ZH: Record<string, string> = {
  'Connected': '已连接',
  'Disconnected': '已断开',
  'Server Config': '服务器配置',
  'Power OFF': '关闭电源',
  'Power ON': '打开电源',
  'Power-on failed': '开机失败',
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
  'Cancel download': '取消下载',
  'Download stalled (no progress for {mins} min)': '下载卡住（{mins} 分钟无进展）',
  'Download stopped before completion': '下载未完成即停止（进程已退出）',
  'Download cancelled': '下载已取消',
  'Cancel download failed': '取消下载失败',
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
  'Python Venv Directory': 'Python 虚拟环境目录',
  'SSH Port': 'SSH 端口',
  'SSH Key Path': 'SSH 密钥路径',
  'API Port': 'API 端口',
  'BMC Username': 'BMC 用户名',
  'BMC Password': 'BMC 密码',
  'Save': '保存',
  'Clear saved credentials?': '清空已保存的凭据？',
  'The saved values for the following will be erased — you will need to enter them again: {fields}':
    '以下已保存的值将被清空，之后需要重新填写：{fields}',

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

  'Console': '控制台',
  '[WebSocket] Reconnect limit reached. Check the AI server and network.':
    '[WebSocket] 重连次数已达上限，请检查 AI 服务器与网络。',
  '✗ Connection error': '✗ 连接错误',

  // Environment deployment module
  'Environment Deployment': '环境部署',
  'locked': '已锁定',
  'Loading...': '加载中...',
  'Recent operations': '最近操作',
  'Roll back driver': '回滚驱动',
  'Roll back vLLM': '回滚 vLLM',
  'Reinstall the previous driver: {pkg}': '重装此前的驱动：{pkg}',
  'Reinstall the previous version: {ver}': '重装此前的版本：{ver}',

  'Pre-flight Check': '环境预检',
  'Re-check': '重新检查',
  'Checking environment...': '正在检查环境...',
  'Partial load failed ({n} of 5) — retry with Re-check.': '部分数据加载失败（{n}/5）— 可用「重新检查」重试。',
  'Some required checks failed — the affected tabs are blocked until fixed.':
    '部分必检项未通过 — 相关 Tab 已阻断，请先修复。',
  'Kernel': '内核',
  'GPU driver': '显卡驱动',
  'CUDA toolkit': 'CUDA 工具包',
  'Python': 'Python',
  'uv': 'uv',
  'Disk': '磁盘空间',
  'ubuntu-drivers': 'ubuntu-drivers',
  'Network': '网络',
  'HF mirror': 'HF 镜像',

  // Preflight values / details — exact English source strings from the
  // backend /preflight endpoint (values embedding live data use the
  // {n} / {hosts} templates via displayPreflightValue).
  'available': '可用',
  'not found': '未找到',
  'unknown': '未知',
  'reachable': '可达',
  'unreachable': '不可达',
  'recommended': '推荐',
  '{n} GB free': '{n} GB 可用',
  'reachable: {hosts}': '可达：{hosts}',
  'no PyPI source reachable': '没有可达的 PyPI 源',
  'driver not installed': '驱动未安装',
  'not installed (optional — vLLM ships its own runtime)': '未安装（可选 — vLLM 自带其运行时）',
  'Open kernel modules need a recent kernel; on Blackwell GPUs the open kernel module is the only supported one.':
    'Open 内核模块需要较新的内核；Blackwell GPU 仅支持 Open 内核模块。',
  'No working NVIDIA driver detected — apply one in the GPU Driver tab.':
    '未检测到可用的 NVIDIA 驱动 — 请在显卡驱动 Tab 中安装。',
  'Below the vLLM recommended minimum driver 575.': '低于 vLLM 推荐的最低驱动 575。',
  'Python 3.10+ (<3.15) is required for vLLM.': 'vLLM 需要 Python 3.10+（<3.15）。',
  'Install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh':
    '请先安装 uv：curl -LsSf https://astral.sh/uv/install.sh | sh',
  '5 GB free space is the minimum for driver/CUDA/vLLM packages.':
    '驱动 / CUDA / vLLM 软件包至少需要 5 GB 可用空间。',
  'The GPU Driver tab requires ubuntu-drivers (Ubuntu only).':
    '显卡驱动 Tab 需要 ubuntu-drivers（仅支持 Ubuntu）。',
  'Configure a reachable mirror in the Mirror Sources panel.': '请在镜像源面板中配置可达的镜像。',
  'Model downloads may be slow/blocked.': '模型下载可能缓慢或被阻断。',
  'nouveau is a fallback driver with poor performance — not suitable for vLLM':
    'nouveau 是性能较差的回退驱动 — 不适合 vLLM',

  'Detected GPU: {gpus}': '检测到 GPU：{gpus}',
  'none': '无',
  'Driver {pkg} installed — reboot the server to activate it.':
    '驱动 {pkg} 已安装 — 重启服务器后生效。',
  'Reboot now': '立即重启',
  'Reboot later': '稍后重启',
  'Driver package': '驱动包',
  'Tags': '标签',
  'Installed': '已安装',
  'Recommended': '推荐',
  'server': '服务器版',
  'open': '开源内核模块',
  'distro': '发行版源',
  'third-party': '第三方源',
  'free': '纯自由软件',
  'non-free': '含非自由组件',
  'Current driver: {version}': '当前驱动：{version}',
  'Current driver: not installed': '当前驱动：未安装',
  'Target driver: {pkg}': '目标驱动：{pkg}',
  'Warning: the existing NVIDIA driver will be removed and the server must be rebooted; running vLLM services are stopped first.':
    '警告：现有 NVIDIA 驱动将被移除且服务器必须重启；运行中的 vLLM 服务会先停止。',
  'Apply driver': '应用驱动',
  'Apply': '应用',
  'Apply driver {pkg}?': '应用驱动 {pkg}？',
  'Reboot the AI server now?': '现在重启 AI 服务器？',
  'The server will reboot to activate the new driver. All services are interrupted until it is back (1-3 minutes).':
    '服务器将重启以启用新驱动，重启期间（1-3 分钟）所有服务中断。',
  'Driver version below the vLLM recommended minimum {min}': '驱动版本低于 vLLM 推荐最低版本 {min}',
  'Driver {major} requires CUDA {floor}+ minimum — incompatible with older CUDA versions.':
    '驱动 {major} 最低支持 CUDA {floor}+，不兼容更旧的 CUDA 版本。',
  'Applying will uninstall the current driver; a reboot is required to activate the new one.':
    '应用将卸载当前驱动；需重启才能启用新驱动。',
  'This is the currently installed driver.': '这是当前已安装的驱动。',

  'Locked: finish the GPU driver deployment first.': '已锁定：请先完成显卡驱动部署。',
  'vLLM ships its own CUDA runtime and works for most models, but some models need the system CUDA Toolkit (nvcc) to compile kernels at startup:':
    'vLLM 自带 CUDA Runtime，多数模型可直接运行；但部分模型启动时需要系统 CUDA Toolkit（nvcc）编译内核：',
  '1. System CUDA Toolkit (recommended — most robust; covers the runtime-compiled scenarios)':
    '1. 系统级 CUDA Toolkit（推荐 — 最稳妥，覆盖需运行时编译的场景）',
  '2. Built-in CUDA Runtime only (lightweight; some models fail to start without nvcc)':
    '2. 仅内置 CUDA Runtime（轻量；缺少 nvcc 时部分模型无法启动）',
  'Currently installed system toolkit: CUDA {version}': '当前系统工具包：CUDA {version}',
  'CUDA version': 'CUDA 版本',
  'Minimum driver': '最低驱动',
  'driver ≥ {ver}': '驱动 ≥ {ver}',
  'installed driver {major} does not support': '已装驱动 {major} 不支持',
  'selected driver {major} does not support': '已选驱动 {major} 不支持',
  'Installed driver {major} supports up to CUDA {max}': '已装驱动 {major} 最高支持 CUDA {max}',
  'Selected driver {major} supports up to CUDA {max}': '已选驱动 {major} 最高支持 CUDA {max}',
  'Advanced options': '高级选项',
  'Custom CUDA version (for testing)': '自定义 CUDA 版本（测试用）',
  'Current driver {major} cannot use CUDA {ver} — go back to the GPU Driver tab and upgrade the driver first.':
    '当前驱动 {major} 无法使用 CUDA {ver} — 请返回显卡驱动 Tab 先升级驱动。',
  'Install system CUDA Toolkit (recommended — most robust)': '安装系统 CUDA Toolkit（推荐 — 最稳妥）',
  'apt install from the NVIDIA repo + PATH / LD_LIBRARY_PATH environment (persisted)':
    '从 NVIDIA 源 apt 安装 + PATH / LD_LIBRARY_PATH 环境（持久化）',
  'Skip system CUDA (may fail without nvcc)': '跳过系统 CUDA（缺少 nvcc 时部分模型可能启动失败）',
  'No system CUDA is installed; the vLLM tab interlocks with the toolkit already on the server.':
    '不安装系统 CUDA；vLLM Tab 与服务器上已有的 CUDA Toolkit 互锁。',
  'Selected: CUDA {ver}': '已选择：CUDA {ver}',
  'Install CUDA Toolkit': '安装 CUDA Toolkit',
  'Confirm selection': '确认选择',
  'Currently installed: vLLM {version}': '当前已安装：vLLM {version}',
  'Latest stable (PyPI)': '最新稳定版（PyPI）',
  'Pinned version': '指定版本',
  'CUDA Runtime binding': 'CUDA Runtime 绑定',
  'Auto-bound: system CUDA runtime, {index} torch backend (matches CUDA {ver})':
    '自动绑定：系统 CUDA Runtime，{index} torch 后端（匹配 CUDA {ver}）',
  'Auto-bound: built-in CUDA runtime, {index} torch backend (matches CUDA {ver})':
    '自动绑定：内置 CUDA Runtime，{index} torch 后端（匹配 CUDA {ver}）',
  'Auto-bound: built-in CUDA runtime, {index} torch backend (no system CUDA detected)':
    '自动绑定：内置 CUDA Runtime，{index} torch 后端（未检测到系统 CUDA）',
  'Selected CUDA {sel} does not match the effective CUDA {eff} — select a matching version in the CUDA tab.':
    '已选 CUDA {sel} 与生效 CUDA {eff} 不匹配 — 请在 CUDA Tab 选择匹配的版本。',
  'Selected CUDA {sel} conflicts with the system CUDA {cur} — the binding follows the system toolkit.':
    '已选 CUDA {sel} 与系统 CUDA {cur} 冲突 — 绑定跟随系统工具包。',
  'No CUDA version selected': '未选择 CUDA 版本',
  'Python environment': 'Python 环境',
  'Python interpreter (uv managed)': 'Python 解释器（uv 管理）',
  'System default': '系统默认',
  'New virtualenv': '新建虚拟环境',
  'Existing virtualenv': '已有虚拟环境',
  'Venv name': '虚拟环境名',
  'Select…': '选择…',
  'Local source build (development)': '本地源码编译（开发用）',
  'git clone + editable install instead of the PyPI wheel':
    'git clone + 可编辑安装，替代 PyPI 轮子包',
  'Register vLLM under supervisor after install (template, autostart off)':
    '安装后注册 vLLM 到 supervisor（模板方式，不自启动）',
  'uv not detected on the server': '服务器未检测到 uv',
  'uv {version} detected': '检测到 uv {version}',
  'Install vLLM': '安装 vLLM',

  'Mirror sources': '镜像源',
  'custom': '自定义',
  'official': '官方',
  'Applied to vLLM / torch installs to avoid download timeouts.':
    '用于 vLLM / torch 安装，避免下载超时。',
  'Custom': '自定义',
  'pypi.org (official)': 'pypi.org（官方）',
  'Tsinghua': '清华',
  'Aliyun': '阿里云',

  'Task center': '任务中心',
  'No deployment tasks yet.': '暂无部署任务。',
  'queued': '排队中',
  'running': '运行中',
  'awaiting_reboot': '等待重启',
  'success': '成功',
  'failed': '失败',
  'cancelled': '已取消',
  'interrupted': '已中断',
  'Cancel task': '终止任务',
  'Task log: {title}': '任务日志：{title}',
  'Task log': '任务日志',
  'Download log': '下载日志',
  'Loading log...': '加载日志中...',
  'Select a task to view its log.': '选择任务以查看日志。',
  'Driver task started — follow it in the Task Center.': '驱动任务已启动 — 请到任务中心跟踪。',
  'vLLM install task started — follow it in the Task Center.': 'vLLM 安装任务已启动 — 请到任务中心跟踪。',

  'Risks and Limitations': '风险与限制',
  'Risk 1: driver change requires reboot': '风险 1：更换驱动必须重启服务器才能生效',
  'Risk 3: open kernel driver': '风险 3：Blackwell 核心 GPU 起，仅支持 open 内核模块',
  'Risk 5: driver purge': '风险 5：应用驱动前会 --purge 全部 nvidia-* 包，不可部分回退',
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
