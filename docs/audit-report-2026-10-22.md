# 全栈对抗审核报告 — 2026-10-22

- **基线**：`4436cff`（master = gitea/master = 生产 v20261021-180713，bundle `index-CkOWNFHe.js` 与本地构建逐字节一致）。工作区仅未跟踪文件（`.playwright-cli/`、`frontend/home.yaml`、`.qwen_scope_diff.txt` 等），无未提交代码改动。审核期间**未部署**任何改动。
- **背景**：2026-10-22 凌晨 .7 的 vLLM 引擎（本项目的模型后端）两崩（01:19/01:31 本地时间），崩溃后进入"僵尸态"（引擎死、API 进程活、`/health` 501）持续 70 分钟无人/无机制恢复；同时 10 个并行审核 subagent 因同一引擎崩溃全灭，审核策略改为**串行**（一次一个 subagent）。本轮"全新全栈对抗审核"即在此背景下执行。
- **范围**：全栈——后端（api / service / executor / config / console / ws / monitor / middleware）、前端（api / hooks / components / stores / i18n）、测试、运维层（Dockerfile / deploy / install / start / CI / tools/server_deploy）。审核对象 = 当前工作树（即生产运行的代码）。
- **方法**：5 视角串行 subagent 对抗审核（安全 / 后端并发 / 远程执行与部署管线 / 前端 / 基础设施，保护不稳定的 vLLM 引擎严格一次一个），**主 agent 对每条发现逐条读码核实**（含 1 条核心证伪 + 2 条降级 + 多项参数化源码实证）；机械化验证（pytest 全量、ruff、vitest、tsc+vite build、eslint）。"全新"口径：subagent 未读任何历史审核报告；主 agent 核实阶段结合既往裁决（B1 威胁模型、2026-10-21 断电事故、2026-10-22 引擎崩溃事件史）去重定级。
- **实证动作**（只读）：.7 现场进程树/GPU 归属实测（经 .9 docker exec 复用后端凭据）、本地 paramiko 5.0.0 源码语义核查（exec_command 超时 / missing_host_key 回调线程）、生产鉴权状态与依赖版本核查。
- **线上状态**：生产容器 = `4436cff`（HEALTHY，日志 0 错误）。.7 引擎现状：`vllm serve unsloth/Qwen3.8-27B-NVFP4 --tensor-parallel-size 4 --speculative-config {"method":"mtp","num_speculative_tokens":3}`（PID 1087058，进程树完整：API → EngineCore → 4×Worker，各 Worker 占 ~14.7GiB 显存，无孤儿进程）。

## 一、机械化验证结果（全绿）

| 检查项 | 结果 |
|---|---|
| `pytest tests -q` | 310 passed + 2 xfailed（xfail = B1 空 KEY /docs 项，按威胁模型接受） |
| `ruff check backend tests` | All checks passed |
| `npm run test`（vitest） | **101 passed**（22 个文件） |
| `npm run build`（tsc -b + vite） | 通过；index-`CkOWNFHe.js` 137.45 kB（与生产 bundle 一致） |
| `npm run lint`（eslint） | **0 errors** |

## 二、P1（阻断级）

### BCK-1 vLLM 引擎死亡（僵尸态）对自愈链完全不可见——watchdog 70 分钟零动作（2026-10-22 事件根因）
- 引擎 core 崩溃但 API 进程存活、端口 8000 开放、`/health` 返回 501（僵尸态）时：status-sync 只用 `GET /v1/models` + TCP 端口 + ps 判活，**从不查 /health** → `is_running` 恒 True → 无 RUNNING→STOPPED 迁移 → 零 crash 事件（model_status_sync.py `_sync_status_once`：`if not is_running and (remote_model_ids or strict_present is False)` 降级门槛在僵尸态永不满足；`_check_vllm_present_strict` TCP 连上即 True）。
- 死锁闭环：即使有 crash 事件，`model_watchdog._attempt_restart` 的入口门 `_check_vllm_present_stright`（TCP 开放 = present=True）会 "skipping auto-start until sync reconciles the status" 直接 return——而 sync 永远对不齐僵尸态。自动恢复路径不存在。
- 事件现场：01:19:11 僵尸态出现，至 01:50:17 恢复，70 分钟窗口 watchdog 动作日志为零；01:33/01:43 两次进程替换为纯人工（无 dashboard 日志记录）。
- **修复**：引入 /health 引擎级存活探针（TCP 连上 + GET /health 非 200 = 僵尸）：① 接入 status-sync 降级门槛（仅 `(has_loading or has_running) and not remote_model_ids` 时额外探测，常态零开销）；② `_attempt_restart` 加僵尸分支（force-kill 后重启，不再等 sync 对账）；③ chat 预检加 /health。LOADING 态不动（避免慢冷启动误判，P2-9 语义保持）。

### REM-1 strict host-key 首连死锁——非重入锁 + 同步回调 → 整个 SSH 通道层永久瘫痪（潜伏地雷）
- `backend/executor/remote/ssh.py`：`_KNOWN_HOSTS_LOCK = threading.Lock()`（非重入）；strict 分支 `with _KNOWN_HOSTS_LOCK:` 包住 `load_host_keys` + `set_missing_host_key_policy` + **整个握手（`client.connect`）**；`missing_host_key` 回调内**再次** `with _KNOWN_HOSTS_LOCK` 做 known_hosts 落盘。
- paramiko 5.0.0 源码实证（本地 `paramiko/client.py` L448）：`t.start_client(...)` 后 `self._policy.missing_host_key(...)` 在 **connect() 调用线程同步执行** → 首次连接一个不在 known_hosts 的主机时回调自锁 → **永久死锁**；且 `_KNOWN_HOSTS_LOCK` 被永久持有 → 之后所有 strict 连接（包括已知名主机，`with` 块第一步 load 就卡）全部卡死——进程级永久中毒，只能重启容器。
- 暴露窗口：git 历史显示该代码自 v1.0.0 基线就在。生产未触发：.7 早已在 known_hosts，且部署数据卷（`-v {DATA_VOL}`）跨容器重建保留 known_hosts。**触发条件：Server Config 切换到新主机 / known_hosts 文件丢失或损坏 / 全新空数据卷首连**——本工具多机（.7/.9/.14）切主机是真实使用场景。
- **修复**：握手移出锁外（`with` 只保护 load 的文件 I/O；回调落盘保留锁与 load 互斥），~3 行改动 + 单测（mock 回调验证锁顺序）。

## 三、P2（现实场景真实问题）

### 后端并发
- **BCK-2 共享 SSH 探测线程的失效语义：stale 缓存 UI 常绿 + 卡死时永不重建**：`ssh_probe.check_ssh_connectivity` 缓存 age>15s 只打 warning（零行为设计）；`if _ssh_probe_thread is None or not _ssh_probe_thread.is_alive()`——线程卡住（alive）时永不重建。2026-10-21 断电事故即"机器已断电 UI 仍绿"形态。*注*：原 agent 报"读端无界永久挂死"核心论断已被 paramiko 5.0.0 源码证伪（`exec_command` 有 `chan.settimeout`，见证伪区）——残留为缓存陈旧语义 + 无自愈。 **修复**：缓存 age 超阈值（如 60s）允许重建探测线程；`refresh=True` 调用点（model_lifecycle 启动路径 / power_ops BMC 验证循环）外包 `asyncio.wait_for`。
- **BCK-3 部署同 kind 重复触发守卫 TOCTOU**：`deployment_entry._reject_inflight`（查 inflight）→ 2-4 个 SSH 往返（get_driver_list / list_installed_driver_packages / CUDA probe / get_vllm_env）→ `task_manager.create`（claim）——检查与 claim 之间无锁；API 层 `asyncio.to_thread` 并发 = 真并行 → 双击/双 tab 产生两条并行管线争 apt/dpkg/uv 锁。**修复**：claim 原子化（task_manager 锁内 check+create 一体）。
- **BCK-4 下载重复触发 TOCTOU + probe 失败 fail-open**：`model_downloads.download_model`：probe（SSH）→ 409 判定（仅 `probe_res.get("success")` 且 EXIST≠NONE 才拦）→ `set_hf_mirror`（SSH）→ `download_model` 启动（SSH）——检查与启动跨 3 个 SSH 往返；probe 失败时**放行**（fail-open）。两个并发下载同一 repo → 两个 `hf download` 写同一 HF 缓存目录。**修复**：check-then-launch 合并为单条 SSH 命令原子化；probe 失败 fail-closed。
- **BCK-5 stop_model 无锁——手动 stop 可被 watchdog 自动拉起覆盖**：`stop_model` 不持 `_start_lock`；locked 模型启动中用户点停止、stop 流程 >10s 时，`_verify_start_probe` 读到仍 LOADING + present=False → 写 STOPPED + crash 事件 → watchdog 10s 后把用户刚停的模型拉起来。违反模块 docstring "a manual stop of a locked model is NOT re-started" 语义。**修复**：stop 与 verify-probe/watchdog 协调（stop 持 `_start_lock` 或在 stop 路径置"手动停止中"标志使 probe 跳过）。
- **BCK-6 get_drivers 同步阻塞 + complete_driver 无幂等守卫**：`deployment_routes_env.get_drivers` 在 `async def` 内直接调 `reconcile_driver_task()`（阻塞 SSH 进事件循环，前端 /state 轮询每次 15s SSH 都卡循环）；30s 定时器与读端点双触发同任务；`complete_driver` 无条件置 success（重复完成）+ `_record_history` 在锁外（双历史竞态）。**修复**：`to_thread` 化 + complete 幂等 + history 入锁。
- **SEC-1 请求 body 无界读取 + 部署状态字符串无上限（内存/磁盘/轮询成本 DoS 面）**：① `middleware.request_log_middleware` 对 /api/v1 的 POST/PUT/PATCH `await request.body()` **全文读进内存**再截 2KB 预览（无 Content-Length 门）；② `PUT /deployment/state` 的 selected.*（driver/cuda/vllm_version）`str()` 转换无长度上限 → 任意长字符串持久化进 deployment_state.json；③ mirror URL 的 query 段无长度上限（另见 REM-10 字符集语义 bug）；④ 放大器：`DeploymentState.get()` 每次 `json.loads(json.dumps(...))` 全量深拷贝，前端轮询 GET /state 反复支付。**修复**：Content-Length 超限跳过 body 日志（条目照记 body=None）；selected.* ≤512 字符；mirror URL ≤300 字符。
- **INF-1 部署管线结构上无法注入/保留 API Key（P1 裁决降级）**：`server_deploy.py` 的 `docker run -e` 清单只有 API_HOST/API_PORT/STATIC_DIR/HF_ENDPOINT/PIP_INDEX_URL——**无 API key**；`DEPLOY_API_KEY` 只用于健康探测头，从不注入容器；`.dockerignore` 排除 `.env`。生产实测（不带 key 调 /api/v1/models → 200）：当前鉴权本就关闭 = B1 已知状态，无"redeploy 丢 key"事件发生——**降级 P2**（管线缺口：未来想开鉴权，每次部署都会静默抹掉；且无任何警告）。**修复**：docker run 透传 `-e VLLM_DASHBOARD_API_KEY`（或 --env-file）+ 未携带 key 时部署输出显式警告。
- **INF-2 部署失败无回滚路径：旧容器已 rm，坏的新容器（--restart always）留在生产**：`server_deploy.py` 先 `docker stop/rm`（毁旧）→ `docker run` 新容器 → 健康门 6×5s 失败仅 `print("DEPLOY_FAILED"); sys.exit(1)`，**无恢复动作**；5174 被坏容器占用（host 网络），回滚需人工按代码指纹找旧 tag（2026-10 迭代期用户实际走过的路）。**修复**：[4/5] 前 `docker inspect` 记录旧镜像 id；失败时打印一行完整回滚命令（保守方案，不自动执行——对齐"关键基础设施不自动动作"的既有裁决风格；自动 rerun 旧镜像作为可选项供裁决）。
- **INF-3 /health 纯存活检查——健康门不覆盖数据卷/static，坏 dashboard 也能 DEPLOY_OK**：`main.py` `health_check()` 无条件返回 `{"status":"healthy","version":...}`（仅进程活着即 healthy，不查 static 存在、不查 /app/data 可写、不查 SSH/BMC）；部署健康门唯一判据是 `"healthy" in body`。宿主数据目录属主异常（root 下 mkdir -p）→ 容器 appuser 写不了 data → 状态/任务/known_hosts 全失败但 DEPLOY_OK。**修复**：/health 加轻量就绪探针（static/index.html 存在 + data 目录可写）。

### 远程执行与部署管线
- **REM-3 reattach 6h deadline 放弃时不杀远端 job、不清远端文件**：`deployment_reattach._watch_remote_job` 的 deadline 分支仅 `_finalize_reattached`（标 failed + "re-run"），**无 `_kill_long`/`_cleanup`**（对照 run_long 的 deadline 分支明确两者都做，注释"the nohup process would keep holding dpkg locks"）。6h 挂起的 job（慢镜像下载）放弃后远端 nohup job 继续持有 apt/dpkg 锁 → 用户按提示重跑必撞锁。**修复**：deadline 分支对齐 run_long 语义（先 `_kill_long` + `_cleanup` 再 finalize）。
- **REM-4 force stop（scoped -9）杀不到 vLLM 的 GPU Worker 孤儿进程——显存不释放且 CLEAN 验证误判已停止（.7 实测确认机制）**：进程树实测（.7，2026-10-22 04:25 只读快照）：`vllm serve`(1087058) → `VLLM::EngineCore`(1087980, 直接子) → 4×`VLLM::Worker_TP0~3`（**孙进程**，ppid=1087980，各占 14.7GiB）；Worker cmdline 实测 = `VLLM::Worker_TP0` **不含模型路径**。stop 命令 scoped 分支 `kill -9 $p; pkill -9 -P $p` 只到**直接子**（EngineCore），Worker 孤儿化；`gpu_cleanup_scoped` 两条件（cmdline 含模型路径 / ppid ∈ main_pids）对 Worker 均不成立；CLEAN 验证（`model_lifecycle._build_stop_wait_command` scoped 分支）同样只认"cmdline 含模型路径"→ 孤儿不可见 → 返回 CLEAN → stopped=True → 状态机误标 STOPPED，显存持续被占。Worker 孤儿后阻塞在父进程命令队列（Python multiprocessing 经典行为，推断但机制链 90% 实证）。当前 .7 无存量孤儿（ppid=1 扫描干净）。**修复**：scoped 分支改递归杀整棵进程树（复用 `_kill_long` 的 kterm 递归模式；目标限定在"cmdline 含模型路径 + vllm"的根进程树上，无误杀面）。
- **REM-5 跨 kind 部署任务无互斥（driver×cuda 并发必撞 apt/dpkg 锁）**：`_reject_inflight(kind)` 只查同 kind；driver 管线（`apt-get remove --purge nvidia-*` / `apt-get install`）与 cuda 管线（`dpkg -i cuda-keyring` / `apt-get install`）都是 sudo apt/dpkg 长任务；UI 无跨 kind 互斥，"应用驱动"后紧接"装 CUDA"是常见操作序列。**修复**：driver/cuda 映射同一"系统写入"互斥域。
- **FE-2 控制台 WS 重连 10 次（≈181s）后永久放弃，无 UI 恢复入口——且实现违反"Always auto-reconnect"用户裁决**：`useConsoleWebSocket` `MAX_RECONNECT_ATTEMPTS = 10`，退避 1+2+4+8+16+30×5s ≈ 181s，耗尽后终端写红字即停；代码注释自述 "Always auto-reconnect（audit 2026-10-20 P2-15，**user ruling**）"——实现与裁决矛盾。触发场景 = 平台自身流程：DriverTab "Reboot now"（UI 文案 1-3 分钟）→ AI 服务器离线期间每次重连被后端秒拒稳定消耗预算 → 重启超 3 分钟 WS 永久死；`useConsoleStore.connected` 全仓无 UI 渲染方，无状态指示、无重连按钮，只能整页刷新。**修复**：去掉重试上限（对齐硬件 WS 的无限退避、30s 封顶），注释同步。
- **FE-1 Modal 的 Escape 关闭整体失效（modalStack 是死代码）**：`Modal.tsx:22` 模块级 `modalStack: number[] = []` **全仓无 push/pop**；L40 `if (modalStack[modalStack.length - 1] !== modalIdRef.current) return`——空栈取尾 `undefined !== 0` 恒真 → 每个 Modal 的 Escape 分支必然 return。注释声明的"仅栈顶响应"栈语义从未实现。所有弹窗（部署/配置/下载/启动配置/Benchmark/确认框）无法 Esc 关闭，无报错。（注：2026-10-21 删 Modal 探针时"modalStack 仅 Escape 用、保留"的注释假设其工作，实际从未工作。）**修复**：mount 时生成唯一 id push 入栈、unmount pop（严格栈语义）。
- **FE-3 Chat 正文自动滚动无"靠近底部"守卫——生成中无法上翻回看**：`ChatTab.tsx:94-98` 每次 entries 变化（流式每个 delta）无条件 `scrollTop = scrollHeight`；同文件思考块滚动（L101-108）有 40px 近底守卫——同文件两套标准。思考模型长回答（默认 --reasoning-parser、CHAT_MAX_TOKENS=131072）生成期间视图每 delta 被拽回底部。**修复**：复用守卫逻辑。
- **FE-4 Toast（z-50）被所有 Modal（z-[95] 全屏遮罩）永久遮挡——弹窗内操作失败零可见反馈**：`Toaster.tsx:17` `z-50` vs `Modal.tsx:68` `z-[95]`。具体失败路径：启动配置 Save 失败（`useModelManager.handleSaveLaunchConfig` catch → showToast，LaunchConfigModal 无内联错误区）→ 弹窗安静留在原地，用户以为已保存、实际启动命令未持久化、后续启动走旧配置；HF token 保存失败（useModelDownload）同理。**修复**：Toast 层级提到 Modal 之上（z-[100]；ApiKeyGate 遮罩 z-[1000] 场景无操作 toast，不受影响）。

### 前端（补充）
- FE-5~FE-12 见 P3 区（P3 级反馈/卫生缺口，其中 FE-8"启动命令 WS 送达失败后端静默回滚"建议随 FE-2 同批处理——同文件同流程）。

## 四、P3（建议项）

### 安全
- **SEC-2** /api/v1/models/launch-config 的 body（start_command 可含 `--api-key`、env_vars 可含 VLLM_API_KEY——`parse_launch_api` 的设计前提）会进请求日志 2KB 预览 → 接入监控面板 GET /monitor/requests 原样可看（body 排除表 `_CREDENTIAL_BODY_PATHS` 只有 /api/v1/config/server 一条）。**修复**：排除表加 launch-config 路径。
- **SEC-3** main.py `FastAPI(...)` 未关 docs；`_requires_auth` 只覆盖 api/、ws/ 前缀 → 即使设 key，/docs、/openapi.json、/redoc 匿名可见完整 API schema（生产实测三者均 200——B1 空 key 状态下的已知后果；设 key 后为增量泄漏）。**修复**：docs_url/openapi_url/redoc_url=None。
- **SEC-4（= REM-6）** 明文 sudo 密码文件 /tmp/.vdb_pw_*（chmod 600）在 reattach 路径无清理覆盖：job_files 只有 log/pid/exit（无 pw），dashboard 重启后 pw 路径不可知；reattach 终态分支（6h 放弃 / VDB_GONE）连 log/pid/exit 也不清。**修复**：job_files 增加 pw 字段 + reattach 清理统一带上（rm -f 容忍不存在）。

### 后端并发
- **BCK-7** hardware WS 僵尸订阅者（客户端硬断电无 RST/FIN）：keepalive send 无超时 → handler 卡住 → finally unregister 不执行 → _clients 泄漏 + 广播每 0.5s 发僵尸连接。
- **BCK-8** get_download_status（sync 路由 → 线程池）在 3s 前端轮询下每次 2-3 个串行 SSH（ps/tail + 多 GB 目录 du -sb + HF API curl），无 in-flight 守卫，N tab = N× 负载。
- **BCK-9** stop_watchdog / stop_driver_reconcile_timer / hardware_broadcaster.shutdown 的 `contextlib.suppress(asyncio.CancelledError, Exception)` 吞掉关闭路径一切异常（真实 bug 无日志）。
- **BCK-13** `_start_probe_tasks`（asyncio 任务集）shutdown 时不取消——容器关闭路径残留。
- **BCK-6（P3 面）** CHAT_TIMEOUT=300s 实为 httpx read timeout（最大无输出间隔）：思考模型长 prefill >300s 零帧 → ReadTimeout 整段生成取消（引擎不稳期高敏感；显式 Timeout + 注释语义修正）。

### 远程执行
- **REM-7** 下载状态 pidfile `kill -0` 无 cmdline 复核（注释自称 "authoritative"）：下载完成/停止后 pidfile 残留（永不清除），pid 被长寿进程复用 → 误报 downloading/stalled → 用户误操作重新下载数 GB（reattach 探针有同等防护，此路径没有）。
- **REM-8** `tail -c +N` 字节游标 + U+FFFD 修正公式对 2 字节多字节断裂不精确：3 字节中文字符恰在轮询间隙写一半（2/3 形态）→ 游标少进 1 字节 → 字符丢失/日志 1-2 字节乱码（不卡死，errors=replace 兜底）。低概率、影响有限，**默认不修**（如修：poll 命令回传已消费字节数替代本地估算）。
- **REM-9** `_read_and_wait_bounded` 顺序读（stdout 到 EOF 才读 stderr）：stderr >64KB 且 stdout 未 EOF → 管道填满双向阻塞 → 墙钟 deadline 误超时 + SIGHUP 杀远端进程（本项目命令集低概率）。
- **REM-10** URL_RE query 段字符类 raw string 笔误：`[^\\s]` 实为"除反斜杠与字母 s 外任意字符"（意图 `[^\\s]`→`\s`）→ query 放行空格/`;`/`` ` ``/`$()`，白名单形同虚设；当前全部使用点 shlex.quote 兜底（无实际注入），防线单点化。**修复**：一个字符 `\\s`→`\s`（与 SEC-1③ 长度上限合并处理）。
- **REM-11** awaiting_reboot 僵尸任务：装驱动 A 不重启直接装 B（B 不被拒——守卫只查 queued/running）→ 重启后 reconcile 只完成 B（target_major 匹配），A 的 target 永不匹配 → A 永远 awaiting_reboot（cancel 也拒绝）→ UI 待验证提示卡死。**修复**：创建新 driver 任务时强制终结既有 awaiting_reboot（标 failed + "已被新任务取代"）。
- **REM-12** `_kill_long` 按 pidfile 递归杀进程树前无 cmdline 复核（reattach 探针有、kill 没有）：job 已退出 + pid 立即被长寿进程复用 + 用户窗口内操作 → 误杀无关进程整棵树（低概率）。

### 前端
- **FE-5** 启动配置弹窗打开失败静默（handleOpenLaunchConfig 空 catch，按钮零反馈）。
- **FE-6** 模型切换确认流"先停失败"时启动被静默跳过（confirmStart：stop 失败仅 stop 侧 toast，无"启动未执行"说明，弹窗已关闭）。
- **FE-7** 硬件卡稳态期（部署 SSH 占用、WS 停摆窗口）数值冻结但仍显示"实时"样式，无最后更新时间/stale 标记。
- **FE-8** 模型启动命令经 WS PTY 下发，WS 建立失败时后端数秒后静默回滚 STOPPED（_verify_start_probe 设计），前端经轮询悄悄把 LOADING 改回 STOPPED——用户全程无提示（状态一致性后端已兜底，纯反馈缺口）。
- **FE-9** 401 拦截器清存储 key 后，ApiKeyGate 输入框仍预填死 key（useState 初始化器仅首次挂载执行）。
- **FE-10** Chat 无清空入口：useChat 提供 clear（含 streaming 守卫）但 UI 未解构、无按钮；24/7 面板 entries 含全量消息+思考块全文只增不减。
- **FE-11** 配置初始加载失败无重试：loadServerConfig 仅挂载时一次，失败后保存被永久禁用（提示"刷新页面"）——24/7 面板一次网络抖动即长期不可保存。
- **FE-12** ConsolePanel 无条件加载 WebLinksAddon（无自定义 handler）→ PTY 输出中的 URL（vLLM 日志/报错/模型名）变可点链接默认 window.open（可信内网风险低，防御项；addon 默认行为按公开文档判定，推断）。

### 基础设施
- **INF-4** 双"deploy"入口语义完全不同（deploy.sh/ps1 = 本地原生 uvicorn；tools/server_deploy.py = docker 生产部署）——按文件名跑 deploy.ps1 以为部署生产。**P2 降级 P3**（本地 venv fastapi 0.138.0 = 生产 lock 0.138.0，依赖漂移面实测不存在；用户流程熟悉，触发概率低）。**修复**：改名 native_install.* 或原生路径改用 lock 文件。
- **INF-5** Dockerfile 构建阶段 pip/npm 无镜像（运行时 -e 注入的镜像不影响 build）：pip/npm 层缓存失效（依赖变更/新 daemon）时构建须直连 pypi.org/registry.npmjs.org，.9 出网受限时 BUILD FAILED（当前靠层缓存/出网可达，本月多次构建成功；**P2 降级 P3**，推断）。**修复**：ARG 传 --index-url / npm registry。
- **INF-6** tar 打包整个工作区（含未跟踪文件，仅按目录名排除）：frontend/home.yaml、.playwright-cli/ 等本地文件每次 SFTP 到 .9；**backend/ 下任何未跟踪文件会经 COPY backend/ 烘进镜像**（当前无此类文件，潜在缺口）。**修复**：git archive 打包或文件级排除。
- **INF-7** `push` 子命令硬编码 `git push origin master`，origin = GitHub（非默认 Gitea）→ 违背"Gitea 默认、GitHub 仅显式要求"裁决。**修复**：改推 gitea remote。
- **INF-8** 基础镜像浮动 tag（node:22-slim / python:3.12-slim）无 digest pin——同 tag 不保证同镜像。**修复**：pin digest。
- **INF-9** ipmitool 烘进镜像 + BMC 电源操作在容器内 lanplus 执行——容器失陷爆炸半径延伸到 .7 硬件级断电（有意功能；B1 状态下的后果注记，与 INF-1 联动）。
- **INF-10** 部署产物仅成功时清理：失败部署 .9 留全源码 tar+解压目录（下次部署自愈）；本地 tar 任何分支都不删。**修复**：try/finally + 两分支清理。
- **INF-11** 30s 健康门固定（6×5s）：冷启动 >30s 误报 DEPLOY_FAILED；`docker stop/rm ...; true` 吞错 → rm 失败（D 态）被掩盖 → "name already in use" 时旧容器已停新容器没起。**修复**：门窗口可配 + rm 后校验容器已消失。
- **INF-12** .dockerignore 只排除 .env/.env.example，无 .env.*.local——backend/.env.local 可被烘进镜像（当前不存在此类文件，推断）。**修复**：.dockerignore 加 .env.*。
- **INF-13** CI docker job 只 build 不 run——启动即崩的镜像要过 CI、到生产健康门才暴露。**修复**：加 smoke（docker run + 轮询 /health）。
- **INF-14** 一行安装脚本不升级已有安装（发现已有安装即跳过下载）——重跑想"升级"实际仍是旧代码。**修复**：--upgrade 参数或显式提示。
- **INF-15** `static/`（原生部署前端产物目录 + static.bak.* 备份）未入 .gitignore——原生部署后 `git add -A` 可能把构建产物入库。**修复**：补 .gitignore。

## 五、证伪 / 校准 / 已知项

### 证伪（agent 报出、主 agent 核实推翻）
- **REM-2 核心论断（探测读端无墙钟 → 永久挂死/状态灯永久陈旧）**：本地 paramiko 5.0.0 源码（`SSHClient.exec_command`）含 `chan.settimeout(timeout)`——`exec_command("echo ok", timeout=SSH_PROBE_CMD_TIMEOUT)` 的 `stdout.read()` 在超时后抛 socket.timeout；叠加 ssh.py 的 P1-3 修复（socket timeout 保留过 SSH 握手），探测每环（TCP connect / banner 握手 / channel open / read）均有界，探测线程不可能永久卡死。残留 2 项已并入 BCK-2（stale 缓存语义 + 无自愈）与 P3（remote_executor docstring "读端无界"注释过时、refresh=True 调用点无独立墙钟）。*注*：2026-10-21 断电事故"探测线程零迁移日志"的根因仍**未证实**（事故现场容器已被部署销毁），本证伪不改变该未决状态。
- **BCK-2 原报"读端无界"部分**：同上（paramiko 5.0.0 chan.settimeout）。
- **INF-4 依赖漂移面**：本地 fastapi 0.138.0 = requirements-lock pin 0.138.0，无漂移（"native 路径 >= 下限解析到更新版本"仅潜在风险，未发生）。
- **BCK-10 / BCK-11 候选项**（_crash_events suspended 空转 / state_machine _schedule_save 1s debounce SIGKILL 丢 1s）：有界且 by design，未报。

### 排除项（FE 视角自报、主 agent 抽核属实）
- 凭据 `***` 掩码回传：`server_config.py` update 路径 `if update_data.get(f) == CREDENTIAL_MASK: del update_data[f]`——不会把密码覆写成掩码。
- /models/runtime-status：AI 服务器离线时返回 200 `status:"disconnected"`，ModelStatusCard 不卡死。
- WS 一次性 ticket：每次 connect 重新铸票，无已消费 ticket 复用、无 ticket 风暴（退避 30s 封顶）。
- xterm 只读裁决合规：`disableStdin: true` 生效、sendConsoleInput 无调用方、复制/选择保留。
- 轮询防乱序：fetchModels / fetchHardwareMetrics 等均有 reqId 防旧响应覆盖新状态。
- XSS：全仓 grep `dangerouslySetInnerHTML|innerHTML` 零匹配，所有后端文本走 React 文本节点。

### 校准（推翻审核提示中的旧假设）
- requirements-lock.txt **逐条 sha256** + CI `pip-audit --strict`——供应链 hash 校验已落实（"无 hash"旧假设不成立）。
- frontend/package-lock.json 已提交 + Dockerfile `npm ci` 走 lock——前端构建可复现。

### 已知项（按既往裁决接受，不报为缺陷）
- **B1 威胁模型**（用户裁决，xfail 锁）：可信内网、允许空 API key；生产实测确认当前鉴权关闭（/api/v1/models、/openapi.json、/docs 不带 key 均 200）。SEC-3/INF-1/INF-9 的增量风险按此模型定级。
- **关机失败静默**（2026-10-21 用户裁决）：confirmShutdownServer 失败不弹 toast——FE 审核已按裁决排除。
- **单 current model 语义**：平台设计（同一时刻一个 vLLM 进程），多模型并跑不在范围内。
- **控制台只读化**（2026-10-20 用户裁决）：disableStdin 为定档语义。

## 六、修复批次建议（供裁决）

按项目发版流程执行：修复 + 测试 → 机械基线 → 部署生产 → verify → **用户人工验证通过** → commit + push gitea master。GitHub 不推（无显式要求）。版本号不动（1.3.0）。

- **批 1（P1 + 裁决违背，最高优先）**：BCK-1（/health 引擎存活探针 + 僵尸分支）、REM-1（握手出锁）、BCK-2 残留（探测线程自愈 + refresh 调用点墙钟 + 过时注释）、FE-2（控制台 WS 无限重连，恢复用户裁决语义）。
- **批 2（P2 高危，体验/正确性核心）**：REM-4（递归杀树）、REM-3（reattach deadline 杀+清理）、REM-5（driver/cuda 互斥域）、BCK-3/4（原子 claim / 原子 check-then-launch + fail-closed）、BCK-5（stop 与 watchdog 协调）、SEC-1（body 长度门 + 字段上限）、INF-1（key 透传 + 警告）、INF-2（回滚命令/自动回滚——方案待裁决）、INF-3（/health 就绪探针）、FE-1（Modal 栈语义）、FE-3（滚动守卫）、FE-4（Toast 层级）。
- **批 3（P2 其余 + P3 择要）**：BCK-6/7/8/9/13/6（P3 面）、REM-6/7/10/11/12、SEC-2/3、INF-4~15、FE-5~12（其中 FE-8 建议随批 1 的 FE-2 同批——同文件同流程）。REM-8/9（低概率日志保真/管道误超时）默认不修，列入观察。
- **每项修复附测试**（后端 pytest / 前端 vitest；vitest 无 globals，组件测试显式 cleanup）；部署 verify 沿用 7 项检查；关键项（REM-4 杀树、BCK-1 僵尸探针）部署后由用户在 .7 上人工验证一次完整停止/启动周期。

## 七、未决事项（等用户输入）

1. **2026-10-22 01:33/01:43 的 vllm 进程重启是否为手动 SSH 操作**（num_speculative_tokens 1→3 的变更归属）——决定事故记忆 ② 的归因写法。
2. **引擎崩溃机制**：本地证据已穷尽（traceback 被 ~500 行控制台 ring buffer 覆盖、无 Xid、无 kernel OOM）；若浏览器控制台标签页的 xterm 5000 行回滚还开着，上翻可能有崩溃 traceback——可作最后一次取证窗口。
3. INF-2 回滚方案裁决：打印回滚命令（保守）vs 失败自动 rerun 旧镜像（激进）。
4. 批 3 中 P3 的取舍（默认全修，REM-8/9 除外；如需裁剪请指定）。
