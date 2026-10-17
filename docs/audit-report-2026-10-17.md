# 全栈对抗审核报告 — 2026-10-17

- **基线**：`8c404a6`（master = gitea/master，工作区干净，仅未跟踪草稿 `.qwen_scope_diff.txt`）
- **范围**：全栈——后端（api / service / controller / executor / config / console / ws / middleware）、前端（api / hooks / components / stores / i18n / styles）、测试、运维层（Dockerfile / deploy / install / start）。重点对象为本轮拉取的 5 个提交（`db8278e..8c404a6`，核心是模型锁 watchdog 新功能 + b1a2a6f `/server/status` 500 修复），并做 2026-10-16 修复项的全量回归抽查。
- **方法**：机械化验证（pytest 双跑、ruff、vitest、tsc+vite 构建、eslint、行数门禁、pyc 陈旧检查）+ 单审多视角对抗（安全 / 正确性与并发 / 性能与运维 / 测试完整性 / 架构规范），关键路径逐行阅读（model_watchdog、state_machine 锁扩展、model_lifecycle 启动语义、model_status_sync 崩溃钩子、console 会话、前端锁交互与 WS 重连、两个新测试文件全文）。
- **线上状态**：生产容器仍运行 v20261016-093834（= 3a4c8d0）；本轮 5 个提交**尚未部署**。

## 审核时长

**开始 00:37 → 结束 00:47（2026-10-17），共 10 分钟。**

## 一、机械化验证结果（全绿）

| 检查项 | 结果 |
|---|---|
| `pytest tests/ -q`（连跑 2 次验证稳定性） | **219 passed + 2 xfailed**（xfail = B1 空 KEY 项，按威胁模型接受）。基线 211+2 → +8：test_model_lock 6 例、test_server_status 2 例 |
| `ruff check backend/ tests/` | All checks passed |
| `npm test`（vitest） | **48 passed**（9 个文件，含 WS ticket 的 client.test.ts 5 例） |
| `npm run build`（tsc -b + vite） | 通过；main chunk **720.89 kB**（>500 kB 既有提示，无代码拆分，见 A-4） |
| `npm run lint`（eslint） | **0 errors，3 warnings**（全部既有：PreflightCard/i18n react-refresh、DeployEnvModal.tsx:43 缺依赖 `dep.state`） |
| 后端行数门禁（.py ≤ 300 行） | 68 个文件，**无超限**，最大 294 行（backend/console/session_manager.py） |
| tests pyc 陈旧检查 | 34 个 pyc，**0 陈旧**（全部映射现存测试源，含新增 test_model_lock / test_server_status） |

## 二、阻断级候选项（P2）

### P2-1 仪表盘重启重放时序缺口：可能向仍存活的模型重复下发启动命令

**触发场景**：带锁模型真实运行中重启仪表盘（这正是模型锁的主用途——保证模型常驻）。

**证据链**：

1. `state_machine._load_state`：仪表盘启动时把持久化状态里的 RUNNING/LOADING 强制改判为 STOPPED；`locked` 的模型记入 `_pending_locked_restarts`（一次性重放清单）。
2. `start_watchdog()`（lifespan）：立即对清单里的模型 `on_model_crash` → 记录崩溃事件并（事件循环已在跑）`create_task(_restart_after_delay)`，延迟 `RESTART_DELAY_SECONDS = 10` 秒。
3. `_watchdog_loop` 第一个 tick 是**立即执行**（非 t+30s）：先 `await _ms._sync_status_once(timeout=3.0)` 再清理事件。正常路径下，若 t0 同步在 10 秒内确认远端模型在跑 → 内存状态回到 RUNNING → t+10 的 `_attempt_restart` 见 RUNNING 即 `_clear`，不重复启动。✓
4. **缺口**：若 t0 同步在 10 秒内无法确认（刚部署的容器 SSH 连接池冷、vLLM API 抖动、strict 探测要 tcp 连接 + SSH ps 往返、或 API 不可达但进程仍活着），内存状态仍是被强制的 STOPPED → t+10 的 `_attempt_restart` 调 `start_model`，而 `_start_model` 的 409 防护**只查内存状态**（`model.status in (RUNNING, LOADING)`），被强制的 STOPPED 不会触发 409 → 启动命令经 console PTY 重发（用户页面开着则 console WS 自动重连、会话已存在，命令真实执行）。
5. **后果**：对仍存活的模型重复下发用户启动命令——最轻是端口冲突的多余进程 + 日志噪音 + 烧掉 1/3 熔断计数；最重是用户的启动命令若自带清理逻辑（如 `pkill -f vllm && vllm serve ...`），**一次部署就会把健康模型杀掉重启**。

**定性**：P2（低概率——受"立即首 tick + 同步通常 10 秒内完成"兜底；但触发条件明确、最坏后果用户可见，且恰好落在锁功能的主使用场景上）。

**建议修复**（二选一或叠加）：
- `_attempt_restart` 启动前做远端进程存在性检查（复用 `_check_vllm_present_strict`）：单锁设计下"远端有 vLLM 进程 ≈ 我们的模型在跑" → 有则跳过启动。此项能完全关闭缺口（同步无法确认时也成立）。注意其多模型泛化语义（有任意 vLLM 进程即跳过），当前单实例部署无碍。
- 或 `start_watchdog` 重放前先 `await _ms._sync_status_once` 且只重放同步后仍为 STOPPED 的模型（仅当同步能得出确定性结论时有效）。

## 三、建议项（不阻断）

- **A-1 看门狗 tick 无条件 30 秒同步**：`watchdog_tick` 每次都先全量 `_sync_status_once(3.0)`，与是否有锁模型无关。AI 服务器宕机时每 30 秒一条 "status sync failed" 警告 → 日志噪音；且常驻远端负载。建议：无锁模型时跳过同步（或至少降级日志级别）。
- **A-2 新特性测试缺口**：`POST /models/lock` 从未在 HTTP 层被打过（400 无启动命令 / 404 未知 id / 互斥响应体均未覆盖）；`_attempt_restart` 的 409 分支与"400 无启动命令 → FAILED + 清事件"分支未测；10 秒延迟路径与事件循环内 `create_task` 分支未测（现有测试均在循环外驱动 tick）。建议与 P2-1 修复同 PR 补齐。
- **A-3 熔断/崩溃/挂起状态全在内存**：仪表盘重启即清零；重放会为"重启前运行中"的锁模型注入合成崩溃事件——10 分钟内 3 次快速重启即可触发误熔断（模型 FAILED + 自动重启挂起）。可手动启动/重新加锁恢复，但用户侧观感差。建议：重放事件不计入熔断窗口，或熔断状态随 state_machine 持久化。
- **A-4 2026-10-16 遗留项仍开放**：前端无代码拆分（main 720.89 kB）；3 条 lint 警告（含 DeployEnvModal.tsx:43 缺依赖）。均既有、非本轮引入，维持观察。

## 四、回归抽查（10-16 修复项 + 新代码规范）

- **10-16 五项修复零触碰**：`git diff --stat 3a4c8d0..8c404a6 -- backend/middleware.py backend/ws/ backend/api/ws_auth.py Dockerfile tools/ tests/test_security_regression.py tests/test_ws_ticket.py frontend/src/api/client.ts` 为空——WS ticket（B2）、install-status 白名单（B3）、telemetry 日志脱敏、部署/安装/启动脚本、相关测试全部未被本轮 5 个提交改动。
- **b1a2a6f 事故重证 09-11 facade 教训**：本轮拉取的提交修复了一个由我方 3a4c8d0 引入的生产 500（`/server/status`——拆分后 `DetailedConnectionStatus` 移动位置，facade 旧引用失效）。修复用直接导入 + 2 条回归测试（test_server_status.py），正是 2026-09-11 丢弃的拆分批次总结过的那类"静态导入移动符号 → 静默失效"。教训再次在生产验证。
- **新代码 facade 合规**：`model_watchdog.py` / `model_status_sync.py` 的共享状态全部经运行时 `_ms.<attr>` 解析（可被测试打补丁）；`main.py` 从定义模块 `model_watchdog` 导入 `start_watchdog/stop_watchdog`（非 facade 转发）；`model_service` 门面 re-export 兼容名。`model_service ↔ model_watchdog` 循环导入安全（模块对象绑定 + 属性访问延迟到调用时）。
- **锁端点安全面**：query 参数传递、`model_id` 走字典查找（无注入面）、配置 API KEY 时受鉴权门禁保护；无 URL 带密钥（WS 仍走一次性 ticket）；前端无新 XSS 面（4 条中文 i18n，英文回落 key 原文）。
- **并发双崩溃计数窗口分析**（未列为发现项）：`_sync_status_once` 的逐模型循环是纯同步段（无 await），两个并发同步对同一 RUNNING→STOPPED 转变双发 `on_model_crash` 的窗口仅 µs 级，概率可忽略，不构成发现。

## 五、总裁决

基线 `8c404a6` **总体健康**：机械门禁全绿且双跑稳定；10-16 全部修复项完好；模型锁新功能架构质量高（单锁互斥在 RLock 内原子完成、重启重放一次性消费、持久化防抖 + 失败退避、409/400 语义分层清晰、facade 规范合规），测试对核心语义（互斥/崩溃重启动/熔断/手动停不重启/恢复清挂起/重启前重放）覆盖到位。

**未决**：P2-1 重放时序缺口（建议部署依赖锁功能的生产验证前修复，改动面小：`_attempt_restart` 加远端存在性检查 + 补 HTTP 层与分支测试）；A-1~A-4 为建议项可后续排期。本报告不阻断部署，但若线上要实际使用"锁 + 自动重启"，建议先落 P2-1。
