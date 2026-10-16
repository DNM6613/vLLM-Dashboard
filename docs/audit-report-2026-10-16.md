# vLLM-Dashboard 全栈对抗审核报告

> 日期：2026-10-16
> 审查方式：MVP开发专家团三方对抗审查（攻击者视角）+ 机械化验证
> 审查人：贝洛奇(后端) / 贾思敏(前端) / 严过关(QA)，大湾区靓仔(项目总监)汇总裁决
> 总裁决：**fail** — 6 项阻断（3 项安全 P0），生产就绪总档 **Bronze**

---

## 一、机械化验证结果（严过关，真实命令输出）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 后端测试 | pytest tests/ -x -q | **156 passed**, 0 failed, 0 skipped (12.5s) |
| 后端静态检查 | ruff check backend/ | 2 errors（UP017 可自动修；SIM105 model_service.py:383 吞异常） |
| 前端测试 | npm test (vitest) | **43 passed** (8 files) |
| 前端构建 | npm run build (tsc + vite) | 成功，TS 零错误；主 chunk 718.85 kB 超 500 kB 限额，无代码分割 |
| 前端 lint | npm run lint | 0 errors / 3 warnings（DeployEnvModal.tsx:43 缺依赖） |
| 反作弊检查 | skip/恒真断言/mock 边界扫描 | 全部通过，断言密度 2.1-2.4/用例，mock 仅在边界层 |

## 二、阻断项（blocking，按严重度排序）

### B1 [P0 安全] 默认配置即危险：0.0.0.0 + 无 Key 零认证 + docs 泄露攻击面
- `backend/config/settings.py:6` 默认监听 `0.0.0.0`；`settings.py:9-12` `API_KEY` 默认空
- `backend/middleware.py:111-112` 未设 key 时全路由零认证——含 `/ws/console`（远程 root shell）、`/api/v1/config/server/shutdown`（断电）、凭据写入
- `backend/middleware.py:114-118` 鉴权名单不含 `docs`/`openapi.json`——即使启用 key，攻击者仍可拉取完整 API 攻击面地图
- **期望**：敏感产品默认拒绝无 key 启动或默认绑 127.0.0.1；docs/openapi 纳入门禁或生产禁用

### B2 [P0 安全] WebSocket URL 明文携带 API Key
- `frontend/src/api/client.ts:32-39` `getWsUrl()` 将 key 拼进 `?api_key=`，所有 WS 均明文入 URL
- URL 会被反向代理/后端访问日志原样落盘 = 凭据泄露面
- **期望**：后端签发一次性短时效 ticket，WS 握手只带 ticket（浏览器 WS 无法自定义 header，需后端配合）

### B3 [P0 安全] 认证后任意远程文件读取（install-status 端点）
- `backend/api/models.py:147-149` → `service/model_service.py:959-961` → `executor/remote/cli_ops.py:59/76/89`：`log_file` 零校验直透 SSH `tail -20`
- 同类端点 `/download/status`（api/models.py:100-103）却有正则白名单——属不一致遗漏
- 认证攻击者可读远程任意文件前 20 行（`~/.ssh/id_rsa`、`~/.bash_history` 等）
- **期望**：install-status 应用同等白名单（如仅允许 `/tmp/cli_install_hf.log`）

### B4 [P0-1 图标] 文本符号充当 UI 功能图标（两名成员独立命中，交叉确认）
- `frontend/src/components/modals/DownloadModal.tsx:54,59` 用 `✓`(U+2713)/`⚠`(U+26A0) 渲染状态徽章
- 项目已锁定 lucide-react 图标库
- **期望**：替换为 lucide `CheckCircle2` / `TriangleAlert`
- （放行：useConsoleWebSocket.ts / strings.ts 的 `✗` 为 xterm 终端流文本，非 UI 图标）

### B5 [测试完整性] 39 个无源文件的陈旧测试 pyc，安全回归覆盖缺失
- `tests/__pycache__/` 存在约 39 个 pyc 无对应源文件：test_security_regression、test_state_machine、test_error_envelope、test_b1_concurrency、test_v7_regression 等
- git 历史无删除记录（88 commits，tracked 恰为现存 14 文件）——源文件从未入库即消失
- **期望**：开发侧书面解释来源；将安全回归/状态机回归/错误封装回归用例补回版本库

### B6 [规范硬门禁] 单文件超 300 行 + xterm 主题硬编码色
- 后端 7 个文件超限：deployment_service.py(1344)、model_service.py(1051)、remote_client.py(452)、api/deployment.py(450)、session_manager.py(394)、deployment_ops.py(392)、model_ops.py(346)
- 前端 `ConsolePanel.tsx:17-63` XTERM_THEME 共 48 个硬编码 hex（全仓仅此一处，其余 token 化质量很好）
- **期望**：按子功能拆分后端大文件；xterm 主题抽到独立 token 模块/CSS 变量层

## 三、交叉验证结论（总监一致性检查）

| 交叉点 | 结论 |
|---|---|
| WS 鉴权 | 贝洛奇"WS 握手校验 api_key 无绕过" 与 贾思敏"WS URL 明文携带 key" **互补不冲突**：鉴权逻辑有效，但传输通道泄露 key 到日志（B2） |
| 图标规则 | 贾思敏与严过关**独立命中同一处** DownloadModal ✓/⚠（B4），交叉确认 |
| 认证可选风险 | 贝洛奇 blocking#1 与严过关评分卡"安全门禁 Bronze：auth 可选关闭" 独立印证（B1） |
| 凭据保护 | 贝洛奇确认 Fernet 加密落盘 + API 回显脱敏 + 命令注入排查无可利用点，与 QA 安全证据一致 |
| 测试真实性 | QA 机械化 156/43 全绿 与 贝洛奇动态验证 156 passed 一致，反作弊检查通过 |

## 四、高价值通过项（明确核查，非走过场）

- **XSS 攻击面为零**：全仓无 dangerouslySetInnerHTML/innerHTML；xterm 输出走 term.write() 纯文本流；i18n 插值经 React 文本节点渲染
- **命令注入排查无可利用点**：所有用户输入拼接点先过字符集白名单或 shlex.quote；ipmitool 密码经 env 传递不进命令行
- **凭据安全基线扎实**：SSH/BMC 密码、api_key、hf_token 落盘全部 Fernet 加密（失败中止不回退明文）；API 响应掩码；日志有 api_key 脱敏 filter
- **前端工程质量高**：全部定时器/WS 有 cleanup；轮询带 in-flight 守卫与 reqId 竞态防护；错误分支覆盖远超 happy-path
- **两个 WS 握手均 hmac.compare_digest 校验**；SSH 连接池 refcount/evict 带身份校验

## 五、advisory（建议项，不阻断）

1. session_manager.py:318 会话创建与 attach 之间的驱逐竞态；:69-71 reader 线程异常静默 break 无日志
2. sudo 密码明文写远程 `/tmp/.vdb_pw_*`（0600 但 job 被 kill 后残留至重启）；HF token 进远程 bash -c cmdline（ps 可见）
3. 401 风暴：key 错误时 5 个轮询器持续打 401，建议 authRequired=true 时暂停轮询
4. HomePage.tsx(353 行) 整店订阅 zustand store，建议切片选择器；i18n/strings.ts 399 行
5. DeployEnvModal.tsx:43 useEffect 缺 dep.state 依赖（闭包过期风险）
6. 主 chunk 718 kB 无代码分割，建议路由级 dynamic import
7. 无速率限制；main.py:28-29 import 私有成员；CORS 缺 PATCH；ruff UP017 可 `--fix`
8. 日志 basicConfig INFO 非结构化 → 升结构化日志方可达 Silver

## 六、生产就绪评分卡（严过关，六维取最低）

| 维度 | 档位 | 说明 |
|---|---|---|
| 构建可重复 | Silver | lock 文件齐全，全链路可复现 |
| 健康检查 | Silver | /health + /server/health + Prometheus /metrics |
| 错误处理 | Silver | 统一错误封装 + X-Request-ID；扣分：model_service.py:383 吞异常 |
| 日志 | **Bronze** | 非结构化，无业务审计日志 |
| 安全门禁 | **Bronze** | 无速率限制、auth 可选关闭（见 B1） |
| 回滚 | Silver | rollbackEnv + systemd；缺回滚演练证据 |
| **总档** | **Bronze** | 商业级要求 Silver，未达标 |

## 七、修复优先级建议

1. **立即**（部署即危险）：B1 默认配置加固 → B3 白名单补齐 → B2 WS ticket 方案
2. **本周**：B4 换 lucide 图标（5 分钟改动）→ B5 补回回归测试并书面解释
3. **迭代内**：B6 拆大文件/收敛 xterm hex → advisory 中的安全项（速率限制、结构化日志）→ 冲 Silver

---

## 修复记录（2026-10-16 第二轮）

> B4 / B5 / B6 已修复并通过机械化复验（总监门禁复验，非成员自述）。

### B4 ✓ 已修复
- `DownloadModal.tsx:54/59` 的 `✓/⚠` → lucide-react `CheckCircle2` / `TriangleAlert`（w-3 h-3 与宿主 text-xs 排版对齐，语义色不变）
- 复验：grep 扫描 0 残留

### B5 ✓ 已修复
- 陈旧 pyc 清零（110 → 25，全部有源文件映射）
- 补回 5 个回归测试文件共 **41 用例 / ~126 断言**：test_security_regression（11，含 3 个 xfail(strict) 锁定 B1/B3 预期行为，修复后自动转绿）、test_state_machine（9）、test_error_envelope（10）、test_v7_regression（3，真绿）、test_b1_concurrency（8）

### B6 ✓ 已修复
- 后端 7 个超限文件全部拆至 ≤300 行（最大 session_manager.py 294），采用"门面保留原路径 + re-export"模式，156 个旧用例零改动；新增 23 个子模块
- 前端 xterm 主题 48 hex 抽至 `styles/terminalTheme.ts` token 层（含色组语义注释），ConsolePanel 降至 ~99 行
- 顺手修复：SIM105 吞异常（model_service → logger.debug 可观测）、UP017

### 修复后机械化复验（总监执行）
| 门禁 | 结果 |
|---|---|
| pytest tests/ -q | **197 passed, 3 xfailed**（156 旧 + 41 新，零失败） |
| ruff check backend/ tests/ | **All checks passed**（基线 2 error 已清零） |
| wc -l 行数门禁 | 无任何 .py > 300 行 |
| npm test（vitest） | **43 passed** |
| B4 残留扫描 | ✓/⚠ 0 匹配 |
| B5 陈旧 pyc 复核 | 无陈旧项 |

**结论：B4 / B5 / B6 三项阻断关闭。** B1 / B2 / B3（安全类）尚未修复，已有 xfail(strict) 测试锁定预期行为，修复时测试自动转绿。
