# 全栈对抗审核报告 — 2026-10-20

- **基线**：`b03a280`（master = gitea/master = origin/master，工作区仅 1 处未提交改动：`frontend/src/components/ui/Modal.tsx` 的 7 行 MOUNT/UNMOUNT `console.warn` 闪烁探针——零行为改动，已随 v20261020-200544 部署生产，等下次复现归因，**用后需删**，不作为发现项）
- **范围**：全栈——后端（api / service / controller / executor / config / console / ws / monitor / middleware）、前端（api / hooks / components / stores / i18n）、测试、运维层（Dockerfile / deploy / install / start / CI / tools/server_deploy）。审核对象 = 当前工作树（即生产运行的代码）。
- **方法**：5 视角并行 subagent 对抗审核（安全 / 后端并发 / 远程执行与部署管线 / 前端 / 基础设施），**主 agent 对每条发现逐条读码核实**（含 2 条证伪、1 条降级为已知项）；机械化验证（pytest 全量、ruff、vitest、tsc+vite build、eslint）。"全新"口径：subagent 未读任何历史审核报告，主 agent 核实阶段结合既往裁决（B1 威胁模型、2026-10-17 看门狗事故史）去重定级。
- **线上状态**：生产容器 = b03a280 + Modal 探针（v20261020-200544，HEALTHY，日志 0 错误）；本轮**尚未部署**任何改动。

## 一、机械化验证结果（全绿）

| 检查项 | 结果 |
|---|---|
| `pytest tests -q` | **281 passed + 2 xfailed**（xfail = B1 空 KEY /docs 项，按威胁模型接受），7.58s |
| `ruff check backend tests` | All checks passed |
| `npm run test`（vitest） | **86 passed**（17 个文件） |
| `npm run build`（tsc -b + vite） | 通过；7 chunks（index 135kB / react-vendor 194kB / terminal 295kB，既有拆分正常） |
| `npm run lint`（eslint） | **0 errors** |

## 二、P1（阻断级，建议本批修复）

### P1-1 前端 toast 无渲染组件——全部操作反馈静默丢失
- `frontend/src/components/ui/toast.ts:32` 的 `subscribeToasts` **全库仅定义、无消费者**（grep 全 frontend 目录确认；git 历史确认 `Toaster` 组件从未存在过——属"从未建过"而非回归）；`showToast` 共 21 处调用点，包括：启动失败、**"Stop timed out: the process may still be running"**、下载失败/取消失败、开机失败、配置保存成功、复制成功/失败、部署成功提示。
- **后果**：上述操作的用户反馈全部不可见（部分路径有 scanMessage/error banner 兜底，但 stop 超时、下载失败等 toast 通道无任何替代显示面）。
- **修复**：补 Toaster 组件（`subscribeToasts` + 渲染，挂 App/HomePage），~40 行 + 测试。

### P1-2 watchdog `_inflight` check-then-add 跨 await + 409 无脑 `_clear`——复活"LOADING 自清崩溃计数"（2026-10-17 10:15 事故变体）
- `backend/service/model_watchdog.py:_attempt_restart`：`if model_id in _inflight: return`（检查）与 `_inflight.add(model_id)`（占位）之间隔着 presence probe 的多个 await；10s 延迟重启任务与 30s tick 并发进入同模型时均可通过检查 → 第一个 `start_model` 置 LOADING → 第二个 409 → 409 分支 `_clear(model_id)` **抹掉崩溃历史与 _suspended**（model_watchdog.py:195）。
- **后果**：启动命令本身坏（OOM 类）时熔断计数每 flap 周期被归零、永远到不了 3 → 无限空转——正是模块 docstring 明令禁止、2026-10-17 10:15 线上空转 ~65 次的同一机制（当时修的是 clear-on-LOADING，本次是并发 409 路径再次破坏不变量）。
- **修复**：`_inflight` claim 原子化（check+add 同一段无 await）；409 分支按当前状态区分——LOADING（自身在飞）保留事件，RUNNING（外部恢复）才 clear。+ 并发回归测试。

### P1-3 SSH 读端无墙钟——sshd 半死时自愈链静默死亡（B1 根因，覆盖 watchdog/启动/reconcile）
- `backend/executor/remote/ssh.py:71`：TCP 连接成功后 `sock.settimeout(None)`，paramiko 握手/读全无界；`exec_command(timeout=)` 只约束 channel open，`stdout.read()` 无界（`remote_probe.py` docstring 已自认 "the read is unbounded"，sshd 间歇不发 banner 为真实发生过的场景）。
- 无墙钟的后台路径：`model_status_sync.py:_check_vllm_present_strict`（`_probe_ps` to_thread）、`model_lifecycle.py:128`（`start_model` 持 `_start_lock` 期间 `check_ssh_connectivity`）、`driver_reconcile_timer.py:_tick`（`reconcile_driver_task` to_thread）、`_deliver_start_command`（console `get_or_create` to_thread）。
- **后果**：sshd 半死（TCP 可达、无输出）时：① watchdog tick 卡死 → 自动拉起循环永久死亡、无日志不上报；② `start_model` 持 `_start_lock` 卡死 → 一切手动/自动启动永久挂起；③ `_inflight` 永不释放 → 该模型自动重启被永久静默禁用。事件循环活着但后台自愈链静默失效。
- **修复**：给 executor 读端（或这几条后台路径）加墙钟上限（参照 `remote_probe.probe_in_thread` 的既有模式），超时释放 `_inflight`/锁并记 warning。

### P1-4 部署管线 `exec` 读端无墙钟——sshd 卡死时任务线程永久阻塞、任务视图永挂 running（P1-3 的部署管线侧）
- `backend/executor/remote_executor.py:133-146`：`exec_command(command, timeout=...)` 后 `stdout.read().decode("utf-8")` 无超时；部署管线 `ctx.exec`（verify timeout=120）与 run_long 轮询（timeout=30）均无线程级上限；`check_cancelled` 只在轮询间隙执行，也到不了。
- **后果**：远端 sshd 卡住 → 工作线程永久阻塞 → 任务停在 running（UI 无进展、cancel 无效）；叠加 P2-7（无重复触发守卫）可再叠线程。
- **修复**：与 P1-3 同一读端修复覆盖（统一方案，一处改动两处受益）。

### P1-5 GitHub CI backend job 缺 pytest——安全回归测试在 CI 无法导入
- `.github/workflows/ci.yml`：`pip install -r requirements-lock.txt`（29 包，**无 pytest**，grep 确认）+ `python -m unittest discover -s tests -v`；`tests/test_security_regression.py:19` 顶层 `import pytest` + 2 个 `@pytest.mark.xfail(strict)`。
- **后果**：unittest discover 导入该文件 → `ModuleNotFoundError` → backend job 报错退出，**全部后端测试在 CI 不跑**，B1/B2/B3 安全回归锁在 CI 失效。origin/master 自 2026-10-17（0c942d8）起含该文件，Actions 应已红（本地无法查 Actions 页面，机制链路已验证）。
- **修复**（二选一）：CI `pip install pytest` 后改跑 `python -m pytest tests -q`（与本地基线对齐，推荐）；或该测试文件改纯 unittest 去掉 pytest 装饰器（xfail 语义需改写，不推荐）。

### P1-6 deploy.ps1 / install.ps1 缺前端构建步骤（sh/ps1 漂移）
- `deploy.sh:46` 有 `[5/5] 构建前端`（npm ci + build + dist→static），`deploy.ps1` 末步即 `[4/4]`、全文无 npm 指令（grep 0 匹配）；`install.sh:91` 有第 5 步，`install.ps1` 编号止于 `[4/5]`。
- **后果**：Windows 宿主按 ps1 原生（非容器）部署 → 5174 仅 API + /docs、无前端 UI，且无 sh 版的降级警告。生产走容器路径（tools/server_deploy.py），此条仅约束原生/备份路径。
- **修复**：ps1 补齐前端构建步骤与降级警告，与 sh 对齐。

## 三、P2（现实场景真实问题，建议随批或下批）

### 后端
- **P2-1 request_log 捕获明文凭据 body 并经 /monitor/requests 回显**：`middleware.py:request_log_middleware` 对 `/api/v1` 的 POST/PUT/PATCH 全量捕获 body 前 2KB（仅排除 `/monitor/` 自身、位于 auth 外层故 401 也记录）；`POST /api/v1/config/server` 的 body 含 4 个明文凭据字段（`api_key/ssh_password/bmc_password/hf_token`，`server_config.py:24-36` 确认，**全 API 层唯一接收凭据的端点**）→ 进 2000 条环形缓冲 → `GET /api/v1/monitor/requests?ip=` 原样返回（`request_log.py:requests_for`），前端接入监控详情弹窗可见请求内容。**修复**：该路径跳过 body 捕获（条目照记、body=None）。现有 `test_middleware_captures_post_body_preview` 锁的是其他路径，不冲突。
- **P2-2 /metrics 标签基数无界 → 内存耗尽 DoS**：`middleware.py:_normalize_metric_path` 对未命中路由的 `/api/` 路径取前 4 段做 label（随机第 3 段 → 每个新值一个新 series）；metrics 中间件位于 auth 外层（401 也计数）；`/assets/<垃圾路径>` 404 同理（raw path 直接做 label）。prometheus_client 按 label 组合只增不删 → 一条 curl 循环即可 OOM 整个 dashboard。**修复**：未匹配路径归一为固定 label（按首段分桶：`/api/...`、`/assets/...`）。
- **P2-3 部署 mirror 命令注入（URL_RE 放行 shell 元字符 + 无引号拼接）**：`deployment_common.py:44` `URL_RE = ^https?://[\w.\-]+(?::\d+)?(/\S*)?$` —— `https://pypi.org/simple/;reboot` 通过 fullmatch；`deployment_pipelines_vllm.py:62/215` `f"--index-url {mirror}"` 无引号拼入 run_long 的 `nohup bash -c "..."` → 远端按 ssh 用户执行注入命令。对照：`cli_ops.py:38` 的 settings 路径有 `shlex.quote`，任务态 mirror 没有。**定级说明**：按已裁决威胁模型（可信 LAN、允许空 key），key 持有者本就能经 console PTY/start_command 拿同权限 shell，注入的增量面有限；但 URL_RE 自称白名单却对元字符形同虚设，是真实校验缺陷。**修复**：mirror 拼命令处 `shlex.quote` + URL_RE 收紧 path 字符集。
- **P2-4 run_long 超时路径不杀远端 nohup job**：`deployment_task_ctx.py:run_long` 的 deadline 分支只 `_cleanup_long`（删文件）不 `_kill_long`（cancel 分支则两者都做）→ 任务标 failed 但远端 apt/pip 继续跑、占 dpkg lock，重跑撞锁失败。**修复**：deadline 分支补 `_kill_long`。
- **P2-5 reattach 终态无条件标 failed（远端步骤成功退出 code=0 也不例外）**：`deployment_reattach.py:_finalize_reattached` 恒 `task.status = "failed"` + "re-run the task"；driver 任务更不恢复 `awaiting_reboot` 态 → 用户不知道要重启服务器。**修复**：code==0 时标 success（或"completed with re-run hint"）并恢复 awaiting_reboot 语义。
- **P2-6 部署任务无重复触发守卫**：`deployment_entry.py` 的 `start_driver_task/start_cuda_task/start_vllm_task` 均不检查同 kind 是否已有进行中任务，API 层无 409（409 仅 cancel 态）→ 双击"应用"产生两个并行管线（dpkg/uv lock 兜底、第二个失败，任务视图混乱 + 双重 stop vLLM）。**修复**：入口层加同 kind 进行中守卫 + 409。
- **P2-7 run_long 轮询 UTF-8 多字节边界 → 日志/进度永久卡死**：轮询 `tail -c +{offset+1}`（**字节**偏移）+ `remote_executor` strict `decode("utf-8")`；若读取边界恰在多字节字符中间（远端 locale 中文时 apt/dpkg/uv 提示语即触发）→ 下一轮从半字符开始解码失败 → `stdout=""` → **offset 不前进** → 之后每轮仍从同一半字符解码失败 → 任务日志停止更新、`VDB_EXIT` 永远读不到 → 挂死到步骤超时（1800/3600s），叠加 P2-4 远端进程不杀。**修复**：解码改 `errors="replace"` 或边界失败时回退 1-3 字节重试。
- **P2-8 hardware broadcaster 串行广播**：`hardware_broadcaster.py:_broadcast` 逐订阅者 `wait_for(send, 3s)` 串行——N 个慢/半开订阅者把 0.5s 采集周期拖到 3N 秒，全体订阅者数据一起延迟（死亡订阅者的隔离与移除是正确的）。**修复**：`asyncio.gather(return_exceptions=True)` 并发发送。
- **P2-9 10s 启动探测把慢冷启动误判为 crash**：`model_lifecycle.py:_verify_start_probe` 固定 10s 后 strict 探测，`present is False` 即回滚 STOPPED +（locked 时）`_watchdog_on_crash` 再记一次事件并排 10s 重启 → 慢启动场景（启动命令延迟出进程）误记假 crash、双启动竞态、用户重试 3 次即可误触熔断挂起。**修复**：`present=False` 延迟复查一次再回滚，或探测延迟自适应。
- **P2-10 CHAT_TIMEOUT=300s 语义与注释相反**：`settings.py:31-34` 注释 "Wall-clock cap for one proxied vLLM chat generation"，实际 `model_chat.py` 以单一 float 传给 `client.stream(timeout=...)` = **read 超时**（相邻两次读取的最大间隔）：思考模型 prefill/长思考 >300s 无 token 输出 → ReadTimeout → 整段生成零产出取消；反之持续吐 token 的长生成完全不受 300s 限制。**修复**：改显式 `httpx.Timeout` 并把语义定为"最大无输出间隔"（注释同步），thinking 场景可放宽 read。
- **P2-11 同步端点 + 前端 3s 轮询放大 SSH 半死影响**：`api/models.py:99` `get_download_status` 为 sync def（FastAPI 线程池执行）内部 `executor.execute` 无墙钟；前端 `useModelDownload` 3s 轮询最多 1 小时 → SSH 半死时线程池 worker 被逐个占满 → 所有 to_thread/sync 端点排队停滞（与 P1-3/P1-4 同根因的放大器）。**修复**：读端墙钟（P1-3/P1-4 统一方案）覆盖 + 轮询可加 in-flight 守卫。
- **P2-12 应用 logger 日志流未挂 `_RedactApiKeyFilter`**：`middleware.py:46-51` `basicConfig` 仅挂 `_RequestIdLogFilter`；`main.py:_build_log_config` 只改 uvicorn LOGGING_CONFIG 副本（uvicorn 0.49.0 无 root 键，dictConfig 不动 root handler）→ `backend.*` logger 走 root handler **不脱敏**；redact 正则仅覆盖 `api_key=`/`ticket=` 两种 URL query 形态。当前树 grep 无活跃打印点（机制缺口 + 潜在雷）。**修复**：`main()` 给 root handler 补挂 `_RedactApiKeyFilter`。

### 前端
- **P2-13 HomePage 每 5s 整树重渲**：`stores/modelStatus.ts` `set({ model: data })` 无相等检查（timestamp 每次变化）+ `HomePage.tsx:48` 直接订阅 `s.model` → 单页顶层树每 5s 全量 diff；下载期间 `downloadProgressInfo` 新对象引用再破 ModelList memo。**与历史闪烁的关系（重要结论）**：modal 开关全是 HomePage 稳定 useState，当前代码**没有** remount 路径——此级联不构成"打开→自动关闭→再打开"机制，与 2026-10-20 一次性闪动未复现的现状一致；探针继续等复现归因。**修复**：HomePage 只订阅 `api_port` 等原始字段或 store 加相等检查。
- **P2-14 console WS `onopen` 不重发终端尺寸**：`ConsolePanel` 的 `sendSize()` 仅在 term open 时发一次（WS 未 OPEN 时 `sendConsoleResize` 是 no-op）；`useConsoleWebSocket` 的 `onopen` 仅 `terminalReset()` + 发启动命令、无 resize → pty 停在默认 **200×50**（`session_manager.py:invoke_shell(width=200, height=50)` 确认）直到下次窗口 resize（RO 触发）。可见度取决于视口宽与 200 列的差距（用户未报错位——视口接近 200 列或 resize 过）。**修复**：`onopen` 时重发当前 `term.cols/rows`。
- **P2-15 console WS 无 RUNNING/LOADING 模型时断开后永不自动重连 + 10 次上限后无恢复入口**：`useConsoleWebSocket:scheduleConsoleReconnect` 回调先查 `currentModels.some(RUNNING||LOADING)` 否则 return；上限 10 次后仅终端写提示 → 终端永久静默、无连接状态指示、无手动重连按钮（恢复只能刷新页面或重启模型）。**修复**：放宽重连条件或补重连入口/状态指示。
- **P2-16 Chat 点 Stop 后 FIFO 队列继续逐条发送**：`useChat.ts:stop` 仅 `abortRef.current?.abort()`；`send` 的 drain 循环只查 `mountedRef` 不查中止意图 → 停止当前生成后队列消息自动续发（Stop 按钮再现需再点）。**修复**：stop 同时清 queueRef / 置 stopped 标志断循环（行为裁决：停止=全部停止 vs 仅停止当前，需你确认预期）。
- **P2-17 无顶层 ErrorBoundary**：全库 grep 无 `ErrorBoundary/getDerivedStateFromError/componentDidCatch`；任何组件渲染异常（后端数据形状异常）→ 整树卸载白屏，24/7 监控页 = 全瘫需刷新。**修复**：App 层加 ErrorBoundary（提示 + 重载按钮）。

### 基础设施
- **P2-18 HEALTHCHECK 失败（unhealthy）无任何后续动作**：`Dockerfile:37-38` HEALTHCHECK 30s/3 次；`tools/server_deploy.py:114` `--restart always` 仅在容器**退出**时重启，Docker 对 unhealthy 不自动重启 → 主进程挂死（存活不响应）时面板瘫痪至人工介入（崩溃退出场景已覆盖）。**修复**：外部 cron 检 unhealthy 重启，或接受现状（文档明示）。
- **P2-19 `.env.example` 未文档化 settings 实际读取的 11 个 env 变量**：差集 = `VLLM_DASHBOARD_SSH_STRICT_HOST_KEY`、`VLLM_PYTHON_PATH`、`API_TIMEOUT`、`BENCHMARK_TIMEOUT`、`CHAT_TIMEOUT`、`STATUS_SYNC_TIMEOUT`、`SSH_CMD_TIMEOUT`、`HARDWARE_PROBE_TIMEOUT`、`VLLM_DASHBOARD_CORS_ALL`、`STATIC_DIR`、`LOG_LEVEL`（代码侧已逐一对照 `settings.py:9-44` + `main.py`/`middleware.py` 确认）。**修复**：.env.example 补全（含默认值注释）。

## 四、P3（建议项，可后续排期）

- **P3-1** reattach 存活探测仅 `kill -0 $(cat pidfile)`，pid 复用误判 LIVE → 任务假 running 最长 6h（`deployment_reattach.py:probe`）。
- **P3-2** `&&` 链中 `sudo cp` 失败时 `rm -f` 被短路 → /tmp staging 残留（`deployment_pipelines_env.py` / `deployment_pipelines_vllm.py`）；`/tmp/vllm-src-vdb` 从不清理。
- **P3-3** 模型下载重复触发无守卫——同一 repo 双 `hf download` 进程、pid/log 被覆盖（`model_downloads.py:31-66` + `model_ops.py:52`；hf 文件锁兜底无数据破坏，`stop_download` 的 `grep -F repo` 能双杀）。
- **P3-4** console 会话无空闲回收——WS 断开后远端 PTY/SSH 按 reattach 设计保留但永不清理（最多 5 个空 shell，`/health` 的 console_sessions 恒非零）；是否加空闲 TTL 属产品取舍。
- **P3-5** 下载/测速按钮同帧双击重复请求（state 守卫非 ref 守卫，`useModelDownload.ts:133` / `useBenchmark`）。
- **P3-6** 401 后不清旧 key，gate 输入框预填失效旧 key（`client.ts` + `ApiKeyGate`）。
- **P3-7** hardware WS 断连但 SSH 正常时 GPU 卡显示陈旧数据无标记（`HardwareCards: live = sshConnected && ...`，顶部状态条无 WS 状态）。
- **P3-8** deploy `getTasks/getTaskLog/cancelTask` 无 UI 调用者——部署任务无法从 UI 取消/看全量日志（死 API，删除或补入口）。
- **P3-9** `useModelManager` 的 scanMessage `setTimeout` 未清理（单页无实际影响）。
- **P3-10** install.sh/ps1 默认 REPO 指向 GitHub——GitHub 仅显式要求才推送，新机器一行安装可能拉到落后版本（改默认 Gitea）。
- **P3-11** setup.py 死代码（构建/启动均不经过，仅 CI lint + tar 携带；install_requires 与 lock 双源可漂移）。
- **P3-12** requirements-lock.txt 无 hashes（29 包版本全锁但内容完整性不锁）+ 文件头再生成注释写错（`uv pip compile requirements-lock.txt` 应为 requirements.txt）。
- **P3-13** start.sh 与 start.ps1 进程匹配模式漂移（ps1 缺 `*vLLM-Dashboard*vite*` 回退模式）+ 5173 dev proxy 残留（生产容器早已删 5173，仅 dev 机残留）。
- **P3-14** ruff `target-version=py311` 与运行时 py3.12、`python_requires>=3.10` 三处不一致（低影响）。
- **P3-15** `close_http_client`（shutdown）与在途 chat/benchmark 流的竞态——流只捕 `httpx.HTTPError`，限 shutdown 瞬间、影响有限。
- **P3-16** sudo 密码含换行时 `read -r` 只读首行 → 认证失败（极罕见）。

## 五、证伪与已知项（核实后不修）

- **证伪-1（subagent 前端视角 F7"CRLF SSE 兼容"）**：`model_chat.py:stream_chat` 用 `resp.aiter_lines()`（httpx 剥离行尾、兼容 LF/CRLF）+ 每条 `yield line + "\n\n"`——后端已对 vLLM 流做行尾归一化，前端永远只见 `data: ...\n\n`，CRLF 场景不成立。（附带理论边缘：多 data 行的 SSE 帧会丢非 data 行，但 vLLM 单行 JSON 帧不触发。）
- **证伪-2（subagent 后端视角 B5"SSH 探测僵尸线程 + 缓存污染"）**：`ssh_probe.py` 现实现每 tick **读当前 config**（`config_manager.get_config()`），改配后旧线程探的也是新 host；`check_ssh_connectivity` 用指纹（host/port/user/pw/key）判定缓存有效——改配 → 指纹不匹配 → 立即 fresh probe（≤1s），无旧 host 结果污染、无"探旧 host 的僵尸线程"。subagent 读错了实现（按旧版推断），不成立。
- **已知-1（subagent 基础设施视角 I4"生产容器无 API key，LAN 全开放"）**：`tools/server_deploy.py:114` 的 `docker run` 确无 key env（API_KEY 变量仅用于部署脚本自身 health gate）——这是 **2026-10-16 已裁决的 B1 威胁模型**（可信 LAN、允许空 key，2 个 xfail(strict) 锁定），非新发现；维持接受。其衍生面（P2-2 DoS、P2-3 注入）已按"威胁模型内增量"单独定级。
- **已知-2**：单 current model 平台语义下 `stop_vllm` 的 global fallback（path 不匹配时无差别杀全部 vLLM + 全部 `multiprocessing.resource_tracker`）大部分"正确"，真实误杀面 = dashboard 外游离 vLLM/无关 python 服务（已有 warning 日志）——记录在案，不单列。
- **已知-3**：Modal 探针（未提交 7 行）用后需删；`frontend/home.yaml`、`.playwright-cli/` 不在 .dockerignore（打包卫生，tar 路径不含 frontend 根文件故无实际影响）。

## 六、总裁决

基线 `b03a280` 机械门禁全绿；架构整体健康（facade 规范、facade 运行时解析、哨兵防自匹配、reattach/幂等设计、whitelist 纪律均在位）。本轮 5 视角共 42 条原始发现，核实后 **6 条 P1、19 条 P2、16 条 P3**，2 条证伪、3 条归入已知项。

**最重的三个问题**：
1. **P1-1 toast 全盲**——用户侧唯一完全无感知的功能缺陷（所有失败提示都在"发"但没人"收"）；
2. **P1-2 + P1-3 自愈链可靠性**——watchdog 并发竞态复活 10-17 事故变体 + sshd 半死时自愈链静默死亡（10-17 两次自动拉起事故的"未修完"面：当时修的是命令送达路径，本次是并发不变量与无界读）；
3. **P1-5 CI 后端全盲**——安全回归锁在 GitHub Actions 从未真正执行过。

**建议修复批次**：
- **第一批（P1 全修 + 低成本低风险的 P2）**：P1-1~P1-6 + P2-1/P2-2/P2-3/P2-4/P2-7/P2-8/P2-12/P2-14/P2-19（均为改动面小、有明确修复方向的确定性缺陷）。
- **第二批（需你裁决行为的 P2）**：P2-5（reattach code=0 语义）、P2-6（重复触发守卫 409 文案）、P2-9（慢启动探测策略）、P2-10（chat 超时语义定档）、P2-13（5s 重渲）、P2-15（console 重连策略）、P2-16（Stop 是否清队列）、P2-17（ErrorBoundary 文案）、P2-18（unhealthy 处置）。
- **P3**：随第二批或单独排期。

本报告不阻断当前生产运行（v20261020-200544 稳定、日志 0 错误）。
