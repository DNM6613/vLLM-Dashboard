# 全栈对抗审核报告 — 2026-10-23

- **基线**：`2eda3ae`（master = gitea/master；生产容器 = `v20261023-171017`，2eda3ae 代码，当日 17:10 部署，已活体复核）。本地工作区干净，审核对象 = 刚自 gitea 拉取的全量最新代码（4436cff→2eda3ae，88 文件，+4665/−528：新增 `backend/monitor/vllm_metrics.py`、`backend/service/vllm_access_log.py`、`backend/service/connection_notes.py`，前端删 Toaster 体系，`deploy.ps1/sh` 改名 `native_install.ps1/sh`，`server_deploy.py` 大改，vLLM 0.32.0 新指标 + kernels 管线步骤，接入面板数据源切 vLLM 原生 access log）。
- **背景**：2026-10-23 拉取 gitea 最新（含 10-22 三批审核修复闭环 + 10-22 夜间至 10-23 的 4 个功能提交：kernels 管线/新指标、状态卡布局定档、删全部 toast、接入面板切 vLLM access log 数据源）后执行的全新一轮「全新全栈对抗审核」。重点 = 10-22 之后新增/重构的代码 + 前两轮修复的完整性复核。
- **范围**：全栈——后端（api / service / executor / config / console / ws / monitor / middleware）、前端（api / hooks / components / stores / i18n）、测试、运维层（Dockerfile / lock / install / start / CI / tools/server_deploy）。
- **方法**：5 视角并行 subagent 对抗审核（后端核心与监控状态 / 远端执行与部署管线 / 安全 / 前端 / 基础设施），**主 agent 对每条发现逐条读码核实**（通读涉事文件全文 + 交叉验证 + 复现实验）；机械化验证（pytest 全量、ruff、vitest、tsc+vite build、eslint）。
- **成稿说明（接手补完）**：原会话在第七节「B 视角（远端执行与部署管线）」结果未落盘时中断。接手核对：`git status` 只有 `?? docs/audit-report-2026-10-23.md`，代码树零改动（唯一未跟踪文件即本报告）→ 一~六节结论与机械化验证结果按原样保留；本次补完第七节，新增 6 条 B 视角发现（**2 P2 + 4 P3**），其中 2 条用隔离临时目录做了实测复现（不触碰生产/本地数据）。
- **B 视角重审（主 agent 接手后逐条读码复核）**：B-1~B-6、7.1 REM 12 项在位、7.3 探针与 7.4 六条证伪全部与代码相符、成立；修正 2 处表述（CUDA 管线无停 vLLM 步骤、前端状态轮询为 5s 非 3s）与 1 处 B 值表述，线上状态按活体复核更新（生产 = v20261023-171017 = 2eda3ae 代码）。
- **实证动作**（只读）：
  - 活体核对 10.131.1.7 vLLM 0.32.0 `/metrics` 的 `vllm:kv_cache_usage_perc` 量纲（HELP 文本「1 means 100 percent usage」+ 实测值 0.3414 → 0-1 比例，**证伪**了「恒显 100%」的疑似问题，见证伪区）；
  - 本地复现 CI lock 一致性检查正则对 `requirements-lock.txt` 的匹配（30 条目 0 命中，见 E-3）；
  - 文件头字节实测（BOM 判定，见 E-9）；
  - **B 视角两条实测复现**（隔离临时目录 + mock，不动生产/本地数据）：① 部署互斥域——存在运行中的 `vllm` 任务时 `_reject_inflight("rollback_vllm")` **放行**（对照组 `driver` vs `cuda` 正确拒绝）；② 部署管线停 vLLM 后一次 `_sync_status_once` 即把锁定模型记为崩溃（`crash_events=1`）并在延迟到点后**自动拉起**（`auto-started: ['dep-victim']`，状态回到 LOADING）。输出见第七节。
- **线上状态**：生产容器 = `v20261023-171017`（2eda3ae 代码；活体复核 verify 7/7 OK，bundle index-Wx3HZPFr.js、/health 1.3.0、容器 healthy，用户人工验证通过）。.7 vLLM 0.32.0 在跑（TP4 + MTP，Qwen3.8-27B-NVFP4），8000 带 API 鉴权。

## 一、机械化验证结果（全绿）

| 检查项 | 结果 |
|---|---|
| `python -m pytest -q` | **400 passed + 2 xfailed**（23.4s） |
| `python -m ruff check .` | All checks passed（0 错误） |
| `npm test`（vitest） | **130 passed**（26 个文件） |
| `npm run build`（tsc -b + vite） | 通过；`index-Wx3HZPFr.js` 139.18 kB（与 2eda3ae 提交记录一致） |
| `npm run lint`（eslint） | **0 errors** |

*注*：vitest 运行时 ChatTab 3 个滚动用例有 React 警告「two children with the same key, `1`」——测试 fixture 用了重复条目 id（仅测试数据卫生问题，非生产代码缺陷，测试全过）。
*接手复核（20:13）*：`pytest -q` → **400 passed + 2 xfailed（21.7s）**、`ruff check .` → All checks passed、`npm test -- --run` → **26 文件 130/130**，与中断前完全一致（代码树零改动）。

## 二、P1（阻断级）

### E-1 部署脚本：旧容器已删后 `docker run` 失败/SSH 异常 → 生产宕机且无任何恢复动作（tools/server_deploy.py:226-231）
- `_run_deploy` 第 [4/5] 步：`_stop_and_remove(c)` 停删旧容器（删净校验通过）→ `remote(c, run_container_cmd(...), timeout=90)` → `if rc != 0: print("RUN FAILED"); sys.exit(1)`——**无回滚**；`remote()` 抛 SSH 异常（部署中途断连）时异常穿透，`deploy()` 的 finally 只清 tar 不回滚。
- 触发场景：daemon 抖动、目标磁盘满（写不了新容器层）、部署中途 SSH 断连——旧镜像还在服务器上可恢复，但脚本既不回滚也不打印手动恢复命令，生产 5174 裸宕。
- 与 10-22 INF-2 的分工：INF-2 覆盖了「容器起来了但 health gate 失败」（自动回滚旧镜像，已验证在位）；**「容器直接起不来」这条路径从未被覆盖**——`_stop_and_remove` 之前的步骤（build 失败）旧容器还活着无虞，`_stop_and_remove` 之后就是裸奔窗口。
- **修复建议**：双容器方案（新容器临时名启动 + health gate 通过后再停旧换名），或至少在此路径自动回滚 `old_image` + 打印手动恢复命令。

## 三、P2（现实场景真实问题）

### 基础设施 / 部署
- **E-2 build_tar 未跟踪新文件静默漏包，无警告（tools/server_deploy.py:64-113）**：`_tracked_files()` 用 `git ls-files`（注释自述不用 `git archive HEAD` 的原因：release 流程先部署后提交），`build_tar` 只对 tracked 且 `os.path.isfile` 的条目入 tar。**2026-10-23 00:44 事故的同构风险**（新文件 `vllm_metrics.py` 未 `git add` → 镜像缺模块 → 容器启动崩溃，当时靠人工 `git add` 重部署解决）——脚本层至今无任何防护：新增 backend/frontend 文件忘记 add → 部署白跑（INF-2 回滚保住生产）；「已跟踪但 worktree 已删除」的文件同样被 `isfile` 静默跳过。**修复**：`git status --porcelain` 比对 SHIP_FILES/SHIP_DIRS 下的未跟踪文件并打印醒目 WARNING（或提供 `--all` 开关）。
- **E-3 CI lock 一致性检查必然失败（.github/workflows/ci.yml:47 区）**：lock 文件每条首行以 ` \` 续行符结尾（`annotated-doc==0.0.5 \`），检查正则 `^([A-Za-z0-9._-]+)\s*==\s*([\w.+-]+)$` 的 `$` 锚点匹配不上。**本地复现：30 个条目 0 命中** → 13 个直接依赖全部被报「lock 缺失」→ 该步 exit 1。lock 文件本身人工逐条核对一致（13 直接依赖版本均 ≥ 最低要求，30 包全带 sha256）——坏的是检查脚本（10-20 P3-12 加哈希续行符时把 CI 检查改坏）。推 GitHub 后 backend job 永久红、守卫形同虚设。**修复**：匹配前 `line.rstrip("\\").strip()` 或正则尾部 `\s*\\?\s*$`。
- **E-4 容器日志无界增长（server_deploy.py `run_container_cmd`）**：`docker run` 无 `--log-opt max-size/max-file`，json-file 默认无上限。前端 `useServerConfig` 1s/2s 轮询 + 模型列表 15s + HEALTHCHECK 30s，每请求留 uvicorn access + request_log 各一行，数十 MB/天累积，数月后挤占 /var/lib/docker 磁盘（与镜像、数据卷同盘）。**修复**：加 `--log-opt max-size=50m --log-opt max-file=5`（回滚路径复用同一函数，自动覆盖）。
- **C-2 Dashboard API key 暴露于 10.131.1.9 进程表与容器 env（server_deploy.py:28,222,293）**：`health_wget()` 把 `--header='X-API-Key: {key}'` 拼进远程 wget 命令行（health gate ~30s 窗口内 .9 上任何本地用户 ps 可见）；`docker run -e VLLM_DASHBOARD_API_KEY={key}` 把 key **持久化**进容器 `Config.Env`（`docker inspect` 可读，至容器重建）。持 key = 完整控制面板（关机/重启/删模型/改配置）。**修复**：key 经 0600 `--env-file` 注入；health 探测从 .9 本地文件读 key，不进命令行。
- **A-1 僵尸态降级分支缺 `is_stopping` 守卫——四个崩溃记录点中唯一漏网（model_status_sync.py:182-190）**：`elif is_running and engine_healthy is False` 分支（BCK-1 新增的 /health 僵尸探针路径）执行 `if model.locked: _ms._watchdog_on_crash(...)`——同函数另两处崩溃记录点（LOADING→STOPPED、RUNNING→STOPPED 阳性证据）均有 `not _ms.is_stopping(model.id)` 守卫，唯此分支漏。**修复**：补一行 `and not _ms.is_stopping(model.id)`。
- **A-2 手动停止清 watchdog 历史不取消已排程的重启任务（model_watchdog.py:70-72）**：`_clear` 只 `_crash_events.pop` + `_suspended.discard`，**不触碰 `_restart_tasks`**；`on_model_crash` 无条件 `create_task(_restart_after_delay)`（sleep 10s → `_attempt_restart`），而 `_attempt_restart` 完全不读 `_crash_events`（只查 locked/status/suspended/_inflight）。场景：T0 崩溃排程 T0+10s 重启 → 用户 T0+5s 手动停止（`stop_model` 的 `_watchdog_clear` 清了历史）→ T0+10s 任务照跑：状态 STOPPED + locked + 无进程 → `start_model` 把用户刚停的模型拉起，违反「锁定模型手动停止不自动拉起」不变量。与 A-1 叠加：停止窗口内的 sync tick 命中僵尸分支还会排程新任务。**修复**：`_restart_tasks` 改 `model_id → task` 映射，`_clear` 时 cancel 对应任务。
- **C-1 无请求级 body 大小门——10-22 SEC-1 实为「日志预览门」，提交说明 over-claim（middleware.py:157-164 + 全仓库）**：全仓库 `413` 零命中。SEC-1 实际实现 = `request_log_middleware` 对 ≥256KB body **不读入日志预览**（10-22 报告 SEC-1 的原始问题「全文读进内存做日志」已修）；但请求本体仍由 FastAPI/Pydantic 完整缓冲进内存——**持 key（或无 key 部署）的 LAN 主机向 `POST /api/v1/models/chat` 等发 GB 级 JSON → OOM**（容器 `--restart always` 自动拉起，故 P2 非 P1；未认证请求被 auth_middleware 在缓冲前 401 拦截，无此面）。**修复**：最外层中间件按 Content-Length 门（≥64MB 直接 413）或 uvicorn 层限制。

### 远端执行与部署管线（B 视角）

- **B-1 部署管线停 vLLM 绕过了模型停止语义 → 锁定模型被判「崩溃」，看门狗在安装过程中把模型自动拉起（实测复现；10-22 停↔拉起死循环的同构剩余路径）**：vLLM 安装 / vLLM 回滚 / 驱动三条管线的第 0 步都是 `ctx.executor.stop_vllm(...)` 直接调执行器（deployment_pipelines_vllm.py:199-204、:250-255；deployment_pipelines_env.py:54-59；CUDA 管线无停 vLLM 步骤），**不经过 `stop_model`**，因此 `model_lifecycle._stopping_models` 全程为空（该集合只在 model_lifecycle.py:242 由 `stop_model` 加入）；而 `model_status_sync.py:171-172`（RUNNING→STOPPED 阳性证据分支）唯一的护栏正是 `not _ms.is_stopping(model.id)`。链路：管线杀进程 → 5s 内前端状态轮询/30s 看门狗 tick 命中「模型不在 /v1/models 且进程不存在」（HomePage 轮询 5000ms → /runtime-status → 后端 `_sync_status_once`）→ 状态翻 STOPPED + `_watchdog_on_crash` → 10s 后 `_attempt_restart` 见「已锁定 / STOPPED / 无进程」→ `start_model` + PTY 投递启动命令 → **vLLM 在 `uv pip install` 重写同一 venv 期间启动**（驱动管线里更是 `apt purge nvidia-*` 期间）。后果链：半写 venv 下 import 失败 → 启动探针 2×delay 后回滚 STOPPED 并再记一次崩溃 → 10s 重试 → 10 分钟内 3 次真实崩溃触发熔断 → 模型被标 FAILED + 「自动启动已暂停」——**升级成功的机器上，用户看到的是一个 FAILED 且不再自愈的锁定模型**（需手动启动 + 重新锁定）。实测：单次 sync 即 `crash_events=1`，延迟到点后 `auto-started=['dep-victim']`、状态回到 `loading`。
  - **修复**：管线第 0 步改为「进入部署停止窗口」——`model_lifecycle` 暴露 `begin_stop_window(model_id)` / `end_stop_window(model_id)`（或直接复用 `_stopping_models` 的 add/discard + 顺手 `_watchdog_clear`），把整个部署生命周期（含异常/取消/finally）包住，部署后是否重新拉起要显式决定并打日志。判据不是「进程没了」，而是「这次停止是谁发起的」。
- **B-2 vLLM 安装 × vLLM 回滚不在同一互斥域（10-22 REM-5 的同构遗漏，实测复现）**：`_reject_inflight` 的域映射写成 `("driver","cuda") if kind in ("driver","cuda") else (kind,)`（deployment_entry.py:60-61），`start_rollback_vllm` 只查 `rollback_vllm`（:210），`task_manager.create` 也只查同 kind（deployment_task_manager.py:87-90）→ **「安装 vLLM 0.32.0」与「回滚到上一版」可同时运行**：两条管线都 (a) 对同一 venv 跑 `uv pip install`（无 venv 级锁，并发写 site-packages → 最终版本不确定/半装）、(b) 各自先杀一次 vLLM、(c) 各自写 `snapshots.vllm`。实测：运行中的 `vllm` 任务不影响 `_reject_inflight("rollback_vllm")` 放行；对照组 `driver` vs `cuda` 正确拒绝。前端也没兜住——回滚按钮 `disabled={dep.busy}` 只覆盖 HTTP 请求在途（useDeployment.ts:37 的 `busy` 是局部请求态），后台任务跑两小时期间按钮始终可点。**修复**：把 `vllm` / `rollback_vllm` 并入同一「venv 写入」互斥域（REM-5 同一手法：一处元组 + 一条测试）。

### 前端
- **D-1 ChatTab 队列编辑态 index 漂移——编辑中删前面的项会覆盖另一条消息（ChatTab.tsx:49,208,238-244）**：`editing` 是裸 index；移除按钮只处理「删的正是正在编辑的项」（`if (editing === i) cancelEdit()`），`removeQueued(i)` 按 index 删项后其余项左移、`editing` 不偏移。队列 [A,B,C] 编辑 B（idx1）时点 A 的 X → 编辑框渲染到 C 行、显示 B 的草稿 → 回车/失焦 `editQueued(1, B文本)` 把 C 覆盖成 B 的文本（**用户输入数据损坏**）。**修复**：移除 i 时 `editing > i → editing - 1`，`editing === i → null`；或改稳定 id 定位。
- **D-2 接入详情弹窗无过期守卫——慢响应跨 IP 到达「标题 B / 内容 A」（ConnectionsPanel.tsx:107-115）**：`openDetails` 的 `setEntries(await getRequests(ip))` 在 await 后无条件写回，不校验 `ip` 是否仍是当前 `detailIp`。开 A（在途）→ 关 → 开 B → A 响应晚到 → 弹窗标题「B」显示 **A 的请求记录**（可能含请求体）。**修复**：await 后 `if (ip !== detailIp) return` 再写入。
- **D-3 控制台 WS 卸载不递增 seq——ticket 在途时卸载后仍新建无人关闭的 WebSocket（useConsoleWebSocket.ts:43,59-69,168-176）**：`connectConsoleWs` 在 `await getWsUrl(...)`（ticket 往返）后才校验 seq；卸载清理只置 `consoleManualCloseRef`/关旧 ws/清定时器，**从不递增 `consoleConnectSeqRef`** → await 返回后 seq 校验通过 → `new WebSocket` 完成握手：`onopen` 置全局 store「已连接」（假）+ terminalWrite，socket 无人 close，服务端 PTY 挂住至超时。触发路径 = ErrorBoundary 捕获渲染崩溃子树 unmount 恰逢 ticket 在途（低概率真实竞态）。**修复**：卸载清理里 `consoleConnectSeqRef.current++`（或 disposedRef 同查）。

### 安全
- **C-3 HF token 出现在 10.131.1.7 进程命令行/环境变量（model_ops.py:102,154）**：`download_model` 把 `export HF_TOKEN='{token}'` 放进 nohup bash -c wrapper（wrapper/hf 进程**整个下载周期**（可达数小时）在 `/proc/<pid>/environ` 携带 token，exec 前启动阶段 cmdline 亦可见）；`get_download_total_size` 把 token 拼进 `curl -H 'Authorization: Bearer {token}'` 命令行（每 repo 每小时 TTL 触发）。已在 .7 取得本地立足点者可窃取 HF token（访问 gated 仓库/账号计费）。单用户主机降低现实性。**修复**：token 经 0600 临时文件（`curl --header @file`）/stdin 传递，避免 export 进长驻进程环境。

## 四、P3（建议项）

### 基础设施 / 部署
- **E-5 Dockerfile pip 未加 `--require-hashes`**：lock 文件 30 包全带 sha256 但 pip 仅在 `--require-hashes` 时校验——注释声称的「lock 固定内容」实际不生效（版本 `==` pin 仍有效；内网 mirror 场景风险低，主要是注释与行为不符）。**修复**：Dockerfile 与 CI 的 pip install 加 `--require-hashes`。
- **E-6 基础镜像浮动 tag**：`FROM node:22-slim` / `FROM python:3.12-slim`——重建可能拉到新基础镜像，不可复现（内网单机每次重建，风险低）。**修复**：pin 具体版本或 digest。
- **E-7 verify 第 5/6 项 wget 不带 X-API-Key（server_deploy.py:317,321）**：配 key 的容器上两项恒 401（report-only，不影响 VERIFY_OK 判定，但误导「7/7 OK」人工判读）。**修复**：复用 `health_wget()` 的 header 逻辑。
- **E-8 并发部署无互斥**：`TAR_LOCAL` 固定名、秒级时间戳 tag、`BUILD_DIR` 共享、无 flock——两个 deploy 进程并发互相覆盖 tar / `rm -rf BUILD_DIR` 毁对方构建上下文 / 同秒同 tag 回滚错镜像（人工双开罕见）。**修复**：锁文件防并发 + tag 加 PID 后缀。
- **E-9 start.ps1 / native_install.ps1 缺 UTF-8 BOM（字节实测）**：PS 5.1 对无 BOM .ps1 按 ANSI（中文系统 GBK）解析 → 所有中文 Write-Host 乱码（纯显示问题；`install.ps1` 有 BOM 对照正常；已核实 GBK 尾字节不产生引号，无解析风险）。**修复**：两文件补 BOM。
- **E-11 start.sh 后端启动失败仍打「Started」横幅**：5174 被占（is_ours 守卫正确地不杀他人进程）→ 后端绑定失败退出 → 脚本打印「Backend process exited unexpectedly」后继续起前端 + 打「Started」+ 后端地址（易误读）。**修复**：后端意外退出时显著提示或退出。

### 远端执行与部署管线（B 视角）

- **B-3 `source_build` 分支静默忽略所选版本 + 复用固定目录导致二次源码构建必失败（deployment_pipelines_vllm.py:95-99）**：该分支 `git clone --depth 1 https://gh-proxy.com/https://github.com/vllm-project/vllm.git /tmp/vllm-src-vdb && uv pip install -e /tmp/vllm-src-vdb` 有两点问题：① 命令里**没有任何 branch/tag/commit**，用户勾了「源码构建」并选 0.32.0 时实际装的是 main HEAD（未来版本），而 `spec = vllm=={version}` 在分支里被完全丢弃——校验步骤随后把 HEAD 的真实版本写进快照，UI 会显示一个与用户选择不符的「已安装版本」；② 固定目录 `/tmp/vllm-src-vdb` **没有前置 `rm -rf`**，第一次构建失败或成功后重试必然 `fatal: destination path ... already exists and is not an empty directory` → 步骤 2 直接失败，且同一 task id 的失败信息把「源码构建」这条路径变成一次性选项。UI 有该开关（VllmTab.tsx:227 checkbox）。**修复**：`rm -rf /tmp/vllm-src-vdb && git clone --depth 1 --branch v{version} ...`（latest 时省略 `--branch`），或明确在 UI 上标注「忽略版本选择，构建 main HEAD」。
- **B-4 部署任务日志无上限、无修剪（deployment_task_manager.py:250-262 + deployment_service.py:40）**：每个部署任务一个 `data/deployment_logs/<task_id>.log`，`log()` 逐行追加；`MAX_TASKS=50` 只截内存/JSON 记录，**日志目录从不清理**（全仓库无 `os.remove`/过期修剪点）。单次 apt/uv 长任务按 2.5s 一轮的 poll 累积，`exec` 每轮最多写入 4KB stdout + 2KB stderr，2 小时的 vLLM 安装可到 MB 级 → data 卷（同容器数据卷，与 connection_notes/model 状态同盘）长期单向增长。与 E-4（容器 json-file 日志无上限）同类，只是这一份在 data 卷上。**修复**：启动时按 mtime 修剪（保留最近 N 个 / 总量上限），或写日志时按 task 截断到固定上限。
- **B-5 重启任务不参与任何跨域互斥（deployment_entry.py:232-236）**：`start_reboot_task` 不做 `_reject_inflight`，`create("reboot")` 只拒第二个 reboot（deployment_task_manager.py:87）。驱动待验证态下重启是正常操作（`awaiting_reboot` 不占 queued/running），但驱动/CUDA/vLLM 管线**运行期间**调 `POST /deployment/reboot` 也会被接受 → 机器在 `apt-get install`/`uv pip install` 中途断电式下线：远端 nohup job 随机器消失，任务以「Background step process exited unexpectedly」失败，而 dpkg 被留在半配置状态（后续安装撞锁，需 `dpkg --configure -a`）。前端只在 `pending_task`（awaiting_reboot）时渲染「Reboot now」按钮（DriverTab.tsx:143-157），所以现实触发面是手工 API / 多标签页；属防御性缺口。**修复**：`start_reboot_task` 先对所有 `driver/cuda/vllm/rollback_vllm` 做 inflight 检查并拒绝。
- **B-6 驱动管线「先 purge 后安装」失败即无自愈（deployment_pipelines_env.py:61-67）**：step 1 `sudo apt-get remove --purge -y 'nvidia-*'` 成功后，step 2 `apt-get update && apt-get install -y {package}` 若因仓库/网络/磁盘失败，任务只是 failed，机器处于**无任何 NVIDIA 驱动**状态（GPU 监控、模型启停全停），而 `previous_pkg` 虽然已在 `start_driver_task` 记进快照（deployment_entry.py:93-101），却只服务于用户事后手动点「Roll back driver」，失败路径既不自动重装也不打印可复制的手工恢复命令。与 E-1（容器部署 `docker run` 失败无回滚）同构，INF-2 只覆盖了「新容器起来但 health gate 失败」。**修复**：step 2 失败时自动 `apt-get install -y {previous_pkg}`（快照已有），或至少在错误信息里给出该命令。

### 后端
- **A-4 / E-10（同一问题）vllm_access_log offset 按解码后文本长度推进（vllm_access_log.py:213 + remote_executor.py:194-203）**：executor strict 解码失败退回 `errors="replace"` 后，非法/截断 UTF-8 字节变 U+FFFD（1 字节→3 字节），`offset += len(out.encode("utf-8", "replace"))` 漂移 → 丢行至文件轮转 TRUNC 重置才自愈。access.log 为 ASCII（ISO 时间戳/IP/请求行/状态码），仅客户端请求含非 ASCII URL 路径时触发，属潜伏问题。**修复**：远程命令回传实际读取字节数（如 `tail -c +N FILE | wc -c` 分路）或 latin-1 1:1 解码。
- **A-5 `_by_ip` 聚合无淘汰策略（vllm_access_log.py:110-113）**：「100 上限」仅是 `connections()` 返回行数截断，`_by_ip` dict 除 `reset()`（轮转/面板重启）外从不清理、count 自进程启动累计——局域网有界（几个 IP），服务暴露公网时随 distinct IP 线性增长（10 万 IP ≈ 15MB）。**修复**：按 last_seen TTL/容量淘汰（如超 200 项淘汰最久未活动）。
- **A-6 停止验证 5s 超时时状态残留 RUNNING 且 `is_stopping` 窗口已关闭（model_lifecycle.py:252-268）**：`stopped=False`（SIGKILL 后 GPU 进程列表驱动回收滞后于 ps，5s 预算耗尽）→ 状态机不置 STOPPED、`_watchdog_clear` 不执行，`finally` 却已 discard 停止标志 → 下一次 sync 见进程消失且 `is_stopping=False` → 锁定模型记 crash → 看门狗拉起（10-22 修掉的同类不变量违规，不同路径；ps 类检查通常 1-2s 即 CLEAN，窗口很窄）。**修复**：`stopped=False` 也置 STOPPED（kill -9 已送达）或延长停止窗口至 sync 确认。
- **A-7 watchdog_tick 的 `_attempt_restart` 裸调用无异常保护（model_watchdog.py:275 + 278-281）**：tick 内 sync 有 try/except，`_attempt_restart` 没有；`_watchdog_loop` 也无保护——任何非 HTTPException 逃逸（如 launch_config 读文件异常）→ 整个看门狗循环静默死亡至面板重启（异常仅以「Task exception was never retrieved」现形）。当前主路径已防护，属防御性缺口。**修复**：tick 内 `try/except Exception` + `logger.exception`。
- **A-8 `stop_polling()` 只置事件不 join 轮询线程（vllm_access_log.py:160-163）**：lifespan 关停后 in-flight SSH 轮询（≤15s）继续跑到进程退出（daemon 线程被杀）——无状态损坏（offset 只在完整消费后推进），轻微资源尾巴（BCK-13 同类，此线程被漏）。**修复**：`stop_polling` 记录线程并 `join(timeout=20)`。

### 前端
- **D-4 hardware WS 卸载后 ticket 返回仍 new WebSocket（useHardwareWebSocket.ts:23,47-52）**：与 D-3 同类——cleanup（`isManualClose=true` + 关旧 ws）不挡 `await getWsUrl` 返回后的建连；socket 保持 OPEN 泄漏、onmessage 继续写全局 store（onclose 触发的重连被 isManualClose 挡住，无循环）。**修复**：await 后先查 isManualClose。
- **D-5 下载防重入窗口漏「已编辑 HF token」路径（useModelDownload.ts:136-148）**：`downloadInFlightRef` 在 `await saveHfTokenRef`（异步间隙）**之后**才置位——token 有改动时同帧双击通过两次 L136 守卫 → 两次 config PUT + 两次 download POST（后端 BCK-4 原子认领去重缓解）。**修复**：置位提前到守卫通过后、任何 await 前。
- **D-6 在途 5s 轮询可把刚保存的备注短暂闪回旧值（ConnectionsPanel.tsx:36-44,86-105）**：保存前已发出的 poll 在 PUT 完成后返回 → `setConnections(rows)` 用旧 note 覆盖乐观更新，至下一 tick 自愈合（服务端值正确）。**修复**：poll 加 reqId 守卫且保存后丢弃更早发出的响应。
- **D-7 BMC 探测失败置 `bmcPrevPowerRef` 为 null → 抖动恢复后 resetModelsAfterPowerOff 二次触发（useServerConfig.ts:100-103）**：机器已关机（已触发一次）→ BMC 断 1-2 tick（ref 被置 null）→ 恢复探测 `power='off'`、prev=null → 重复 POST（近幂等，属非预期重复触发）。**修复**：仅 `status.power !== null` 时更新 ref。
- **D-8 ZH 界面硬编码英文**：`apiErrorMessage` 兜底 `'Request failed'`（deployment.ts:211，后端错误体不可解析时 ZH UI 显英文）；`aria-label={`CUDA ${v}`}`（CudaTab.tsx:103）；`aria-label="Detailed Metrics"`（ModelStatusCard.tsx:150，提交记录注明为测试钩子刻意保留，ZH 读屏仍读英文）。**修复**：加 i18n 词条 / 测试改按 role 定位。
- **D-9 24/7 转录无上限 + 每 token O(n)（ChatTab.tsx:121-131 + useChat entries 无界）**：entries 从不清理（仅手动 clear）；「发新消息无条件跳最新」的 `entries.reduce` 每条目变化（流式=每 delta）全量执行 + `entries.map` 全量渲染——长期挂机后渐进式卡顿（非立即崩溃）。**修复**：ref 记录见过的最大 user id 替代 reduce；渲染窗口化（保留最近 N 条渲染）。
- **D-10 fetchModels 每 15s 两次无谓全树重渲染（stores/model.ts:43-53 + HomePage.tsx:62）**：`fetchModels` 无条件 `set({loading:true,error:null})` → `set({models,loading:false})`；HomePage `useModelStore()` 无 selector 订阅整 store → 数据无变化也整页重渲染两次/15s（modelStatus store 有 `_sameModel` 等价守卫，这里没有）。**修复**：已有列表时跳过 `loading:true`，或 HomePage 按字段 selector 订阅。

### 安全
- **C-4 launch-config（可含 `--api-key`/VLLM_API_KEY）明文落盘（model_launch_config.py:230）**：`_save_config_locked` 直接 `json.dump`（tmp+os.replace 原子但**不加密**），而 server_config 四凭据 Fernet 加密——.9 本地读权限者可从 `data/model_launch_configs/*.json` 读出远端 vLLM api_key（缓解：该 key 本就由 runtime-status 有意回显，增量影响有限）。**修复**：api_key 段纳入加密或目录 chmod 700。
- **C-5 WebSocket 文本帧无大小上限（session_manager.py:222-244, hardware_broadcaster.py:151）**：`receive_text()` 完整缓冲后才 json.loads + 64KB 检查（检查在缓冲之后）；/ws/hardware 的 receive 循环完全不做大小检查——LAN WS 客户端发超大帧放大内存（设 key 后需先持有 ticket，风险下降）。**修复**：`receive()` 先校验大小再解析，超限 close。
- **C-6 `model_repo` 等 Query 参数无长度上限（models.py:168+）**：字符集白名单 `[\w.\-:@~/]+` 不限长（数 MB 串可过）→ shlex.quote 后 ~5MB SSH 命令（贴近远端 ARG_MAX）+ 驻留 `_dl_total_cache`/`_dl_status_inflight`（chat body 的 model_id/messages/max_tokens 均有界，仅 query 参数漏）。**修复**：Query 参数加 max_length（model_repo ≤256 等）。
- **C-7 前端 key 存 localStorage + 明文 HTTP 传输（apiKey.ts + client.ts）**：`vllm_api_key` 明文 localStorage；生产 `http://10.131.1.9:5174` 无 TLS，key 每次请求明文过 LAN。现实性低（嗅探需同 L2 主动 ARP MITM + 操作员正在用 UI；key 在 .9 的 .env/容器 env 本就可得，嗅探不构成新获取途径）。**修复**：反代 TLS 或文档声明风险。

## 五、证伪区（疑似问题经核实不成立）

- **vllm_metrics `kv_cache_usage_pct` 恒显 100%（subagent 报 P2 未确认）**：`pct = max(values) * 100.0` 假定源值 0-1 比例，但指标名 `*_perc` 与 vLLM help 文本暗示可能是 0-100。活体实证：10.131.1.7 vLLM 0.32.0 `/metrics` 的 HELP 文本明确「**1 means 100 percent usage**」，实测值 `0.3414043583535109`（0-1 比例）→ `*100.0` 解析**正确**，非缺陷。测试夹具（0.42→42.0）与真实量纲一致。
- **vllm_access_log 增量轮询丢行/重复行（轮转窗口疑点）**：`tail -c +N` 1-based 语义与 offset 推进算术在字节精确读取下正确；TRUNC_MARKER 仅在 `S < offset` 触发且此时 tail 必为空（越界 EOF）→ reset 后从头读新文件，轮转无丢行，至多一个轮询周期延迟。ACCESS_LINE_RE 对新 ISO 格式、legacy console 包裹格式、plain uvicorn 格式三种均命中（逐行手工验证）。
- **connection_notes 路径穿越**：`{ip}` 仅作 JSON dict 键、从不拼入文件路径；endpoint `{ip}` 为单段路径参数无法含 `/`；原子写 tmp+os.replace + 损坏文件 `_load` 返回 `{}` fail-safe。不可行。

## 六、已核实无问题（主 agent 抽查确认）

- **部署脚本**：旧镜像在 stop/rm **之前** `docker inspect` 记录；INF-2 health gate 失败自动回滚（同参数 rerun 旧镜像 + 回滚 health gate）在位；`_stop_and_remove` 删净校验（锚定正则 `^vllm-dashboard$`，残留即 raise 中止）；`deploy()` finally 全终结路径清理本地 tar + 远端 tar/BUILD_DIR（镜像保留供取证）；SERVER_PASS 仅从环境变量读取（非 argv）、全程不打印；tar 只含 git tracked 的 Dockerfile/.dockerignore/requirements-lock.txt/backend/frontend 五者，.git/.env/data 永不入包；health gate 可经 DEPLOY_HEALTH_ATTEMPTS/INTERVAL 扩展、降级 503 body 不含 "healthy" 正确判失败；DEPLOY_API_KEY 同时传 wget header 与容器 env（与 C-2 暴露面是同一设计的两面）。
- **vllm_metrics 解析链路**：直方图均值有 `if c > 0` 除零保护；缺失指标 None；畸形文本兜底全 None；别名兼容表同时匹配 family 名带/不带 `_total` 两形态；counter 回绕由 `value >= prev` 守卫处理。
- **watchdog 既有修复在位**：BCK-1 僵尸分支（force-kill + 重启）、breaker（LOADING 不清计数、synthetic 不计次）、`_inflight` claim-before-await、P2-1 presence 探针、BCK-9 关停只吞 CancelledError——逐一验证在位（新增缺口见 A-1/A-2/A-7；**看门狗本身无缺陷，但「谁算崩溃」的输入侧还有一条漏网：部署管线停 vLLM 未进停止窗口 → B-1**）。
- **远端执行与部署管线的既有修复**：REM-1/3/4/5/6/7/10/11/12 逐条对码在位（明细见 §7.1），SSH 池引用计数、`run_long` 三态轮询、下载去重（BCK-4 原子认领 + fail-closed）、awaiting_reboot 幂等终结均已复核（本轮新增缺口 B-1~B-6，B 视角证伪见 §7.4）。
- **认证/密钥**：`/api/v1` + `/health` + `/metrics` 设 key 后全覆盖；WS 走一次性 ticket（secrets.token_urlsafe(32)、单次消费、60s TTL、4096 上限）；`hmac.compare_digest` 恒定时间比较；路径变形（百分号编码/双斜杠）无法绕过鉴权；无 key 全开放为既定设计。
- **shell 注入面**：用户可控参数全部 shlex.quote 或字符集白名单（model_repo/save_path/venv/mirror/version/log_file/scan 目录）；notes 内容（≤200 字符任意文本）不流入任何远端命令（仅 JSON dict 键）；sudo 密码经 channel stdin、BMC 密码经 IPMI_PASSWORD env（ipmitool -E）。
- **路径穿越**：notes 固定路径 + JSON 键；launch-config 文件名 `_safe_id` 替换 `/` `\`；delete_model 安全前缀 + readlink -f 复验 + 深度三层校验；download log_file 正则 + normpath 双校验。
- **XSS**：frontend/src 全库无 `dangerouslySetInnerHTML`/`innerHTML`；notes/模型名/请求面板 React 默认转义；控制台输出走 xterm `term.write`；chat 纯文本渲染。
- **凭据存储/日志**：server_config 四凭据 Fernet 0600 + fail-closed；`_RedactApiKeyFilter` 脱敏 uvicorn/root 日志；500 通用文案不带栈；request_log 无对外端点（monitor 端点用 vllm_access_log，无 body 字段）；.dockerignore/.gitignore 排除 .env/data/.git；tar/镜像无凭据泄漏面。
- **CORS**：默认仅 localhost/127.0.0.1:5174 同源，`CORS_ALL` 显式 opt-in。
- **lifespan 关停顺序**：watchdog（cancel 主 task + 全部 restart task）、driver timer、access_log 轮询、start 探针、hardware broadcaster、http client 依次停止（遗留见 A-8）。
- **容器运行时**：非 root（USER appuser + /app/data chown）；EXPOSE/HEALTHCHECK 与 API_PORT 一致；healthcheck key 环境变量名与 settings AliasChoices 一致；ipmitool 确被 power_ops 使用（非死重量）。
- **lock 文件一致性（人工逐条）**：13 直接依赖版本均 ≥ requirements.txt 最低要求，30 包全带 sha256——文件本身一致（坏的是 CI 检查，见 E-3）。
- **install/start 脚本**：.env 仅从 .env.example 生成不覆盖；`is_ours`/`Test-IsDashboardProc` 只杀本项目进程（端口冲突不伤他人）；install 对已有安装跳过下载并明示不升级；native_install venv 重建有确认、幂等；install.ps1 有 BOM + SHA256 校验 + 内网 TLS 策略。
- **access.log 资源**：50MB×7 轮转在 .7 的 /home/uforce/vllm_logs/，dashboard 容器仅 SSH 只读 tail，不在数据卷、不撑爆 /app/data；`_line_buf` 64KB 上限；轮询 single-flight、异常后继续轮询不停摆、文件不存在每轮无害。
- **Modal / ChatTab 滚动 / 控制台 WS 重连 / ModelStatusCard / i18n 词条**：10-22 修复的 FE-1（mountedModals + compareDocumentPosition 嵌套/同帧场景全对）、FE-2（无限重连 + 30s 封顶 + 四路径定时器清理）、FE-3 滚动意图重写（内容增长不触发 scroll 事件、发新消息无条件跳最新、思考块 40px 守卫）、5 新指标 null 安全、折叠区 state 控制、aria-expanded——逐一通读复核无回归（新缺口见 D-1/D-3/D-9/D-10）。
- **powerState/deriveServerOn**：BMC powerFresh 权威、否则回退可达性，与 2026-10-21 断电事故裁决一致。
- **ApiKeyGate**：401 后 re-seed、成功整页 reload、拦截器清死 key 与 gate 组件态不冲突。

## 七、B 视角（远端执行与部署管线）— 本轮补完

### 7.1 上轮 REM 项在位复核（逐条对码）

| 项 | 状态 | 取证位置 |
|---|---|---|
| REM-1 strict host-key 首连死锁 | **在位** | ssh.py:88-101（锁只包 `load_host_keys`，握手在锁外；注释留痕） |
| REM-3 reattach 6h deadline 不杀 job | **在位** | deployment_reattach.py:153-161（先 `_kill_long` + `_cleanup` 再 finalize，与 run_long 对齐） |
| REM-4 scoped 杀不到 Worker 孙进程 | **在位** | process_ops.py:44-48（后序递归 `kterm`）+ model_lifecycle.py:66-73（CLEAN 判定加 `worker_cnt`，哨兵过滤正确） |
| REM-5 driver×cuda 互斥 | **在位（但仍有同构缺口 → B-2）** | deployment_entry.py:60-61 |
| REM-6 sudo pw 临时文件无清理 | **在位** | deployment_task_ctx.py:203-213（job_files 含 pw）+ reattach.py:89-91,140,150,159 |
| REM-7 下载 pidfile 无 cmdline 复核 | **在位** | model_download_status.py:49-61 |
| REM-8 `tail -c` 偏移按解码长度推进 | **保留不修（用户裁决）** | deployment_common.py:81-90；部署日志以 ASCII 为主，3 字节断裂仍属已知取舍 |
| REM-9 顺序读 stdout/stderr | **保留不修（用户裁决）** | remote_executor.py:43-47 |
| REM-10 URL_RE query 段 `[^\\s]` 笔误 | **在位** | deployment_common.py:58-62（`(\?[^\s]*)?`） |
| REM-11 awaiting_reboot 僵尸任务 | **在位** | deployment_entry.py:78 + deployment_task_manager.py:222-247（幂等、history 在锁内） |
| REM-12 `_kill_long` 无 cmdline 复核 | **在位** | deployment_reattach.py:201-211 |

### 7.2 本轮新增（明细见 §三 / §四）

2 条 P2：**B-1**（部署管线停 vLLM 未进停止窗口 → 锁定模型被判崩溃、看门狗在安装中拉起）、**B-2**（vLLM 安装 × 回滚互斥缺口）。4 条 P3：**B-3**（source_build 忽略版本 + 固定目录不可重跑）、**B-4**（部署日志无修剪）、**B-5**（reboot 无跨域互斥）、**B-6**（驱动 purge 后无自愈）。

### 7.3 实测复现（隔离临时目录 + mock，未触碰生产）

```
PROBE 1  deployment mutex domains
  inflight(vllm)         : dep_..._7c8592ae
  inflight(rollback_vllm): None
  -> rollback_vllm ALLOWED while a vLLM install is running  <== GAP
  control driver vs cuda: blocked -> CUDA task is already in progress ...

PROBE 2  deployment stop -> crash -> watchdog auto-restart
  before: status= running  locked= True  is_stopping= False
  after sync: status= stopped  crash_events= 1  is_stopping= False
  watchdog auto-started the model mid-install: True ['dep-victim']
  final status: loading
```

脚本为一次性探针（临时 `TASKS_FILE`/`LOG_DIR`/`STATE_FILE` + `mock.patch` 替换 HTTP/启动通道；脚本位于 `%TEMP%\vdb_audit_b_probe.py`，未入库），执行后已还原 `data/models_state.json`（复核 `grep -c dep-victim = 0`）并从快照移除探针模型；生产容器与 10.131.1.7 均未被写入（仅 probe 2 早期一版曾发出一次只读 TCP/`/health` 探测，已改为全 mock）。

### 7.4 B 视角证伪区（疑似问题经核实不成立）

- **`_kill_long` 的 cmdline 复核恒不命中 → 超时/取消杀不掉远端 job**：不成立。wrapper 自身 cmdline 里带的是 `> {log_file}.exit`（`run_long` 的 wrapper 末句），而复核用的 `B=${PFILE%.log.pid}` 是 `{log_file}` 去掉 `.log` 的日志基名，属**超串命中**；日志重定向由父 shell 处理、不进 wrapper cmdline，所以复核依赖的恰恰是 `.exit` 串。逐字符推演 + 与 reattach 探针（同一 `B` 语义）互证。
- **reattach 会重复挂起已结束的旧任务**：不成立。`_recover` 先把 queued/running 改 `interrupted`，候选只取带 `job_files.pid` 的 interrupted 任务，再经 `kill -0` + cmdline 双条件；终态任务不进候选。
- **run_long 的 `VDB_GONE` 三振判定会把「退出后 exit 文件晚写」误判为异常退出**：不成立。轮询命令先 `if [ -f exit_file ]; then cat`，只有 exit 文件不存在时才看 `kill -0`；三振需要连续 3 轮（≥7.5s）既无 exit 文件又无进程。
- **kernels 步骤（0.32.0 新增）失败会拖垮整条管线**：不成立。`_install_precompiled_kernels` 只 catch `DeploymentError` 打 warning，`TaskCancelled` 照常传播；`tests/test_deployment_service.py::TestInstallPrecompiledKernels` 4 用例已覆盖（成功/失败非致命/取消传播/全管线集成）。步骤序号 5/6 步两态也对齐（`auto_register_service` 才加第 6 步）。
- **`bash -lc 'source {venv_path}/bin/activate'` 里的 `$HOME` 会在外层被吃掉**：不成立。`run_long` 经 `_escape_double_quoted` 把 `$` 转义为 `\$`，`bash -c` 收到字面 `$HOME`，由内层 `bash -l` 展开（与既有 verify/同族命令同一机制，且已在生产 kernel 步骤跑通）。
- **SSH 池引用计数可能双重释放/泄漏**：不成立。`get` 在 `_get_or_create_locked` 内 +1；失败路径与 `evict` 各 -1；`_open_and_exec` 的 finally 在 evict 情况下释放的是 `fresh`，与 `evict(ssh)` 一一对应。

## 八、汇总与建议

| 级别 | 数量 | 条目 |
|---|---|---|
| P1 | 1 | E-1 |
| P2 | 13 | E-2 / E-3 / E-4 / C-1 / C-2 / A-1 / A-2 / **B-1** / **B-2** / D-1 / D-2 / D-3 / C-3 |
| P3 | 26 | E-5~E-9、E-11（6）/ **B-3~B-6（4）** / A-4~A-8（5，A-4=E-10）/ D-4~D-10（7）/ C-4~C-7（4） |
| 证伪 | 9 | §五 3 条 + §七 6 条 |

- **机械化验证**：接手后复核 —— `pytest -q` 400 passed + 2 xfailed（21.7s）、`ruff check .` All checks passed、`vitest` 26 文件 130/130；tsc+vite build 与 eslint 沿用中断前结果（代码树在中断期间零改动）。
- **建议批 1（先做，都是「一次操作就把生产/不变量打穿」且集中在部署链路）**：**E-1**（旧容器删掉后 `docker run` 失败/SSH 断开 → 裸宕无回滚，双容器方案）+ **B-1**（部署停止窗口）+ **B-2**（`vllm`/`rollback_vllm` 同域互斥）。这三项一起改，正好把「部署一次 = 停 vLLM + 写 venv + 换容器」这条链上的三类事故都堵住。
- **建议批 2（真实场景 P2）**：E-3（CI lock 检查必红，一行 `rstrip("\\")`）、D-1（队列编辑数据损坏）、A-2（手动停止不取消已排程重启）、C-1（body 大小门）、E-2（未跟踪文件静默漏包）、E-4（容器日志上限）、C-2（API key 进 env/命令行）、A-1（僵尸分支补守卫）、D-2/D-3（前端竞态）、C-3（HF token）。
- **建议批 3（P3 择要）**：B-3/B-5/B-6 各自是一两行的修复（`rm -rf` + `--branch`、reboot 加互斥、失败自动重装上一版驱动），性价比高；其余按既往裁决择要。
- **保持观察（不修）**：REM-8 / REM-9（已裁决）、E-6 / E-9（环境性）、A-5（内网有界）。
- **边界声明**：本轮为只读审核，未修改任何生产代码/配置/数据；审核对象即 `4436cff`→`2eda3ae` 的代码差异（生产容器已为 v20261023-171017 = 2eda3ae 代码，当日 17:10 部署，已活体复核）。修复需用户裁决后分批实施（沿用既往「一次一批 + 每项附测试 + 部署后人工验证」节奏）。
