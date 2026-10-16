# 全栈对抗审核报告 — 2026-10-15

- 审核对象：`master` HEAD `0bac2b0`（工作树干净，审全量代码而非 diff）
- 代码规模：backend 38 个 .py / frontend/src 69 个文件 / tests 13 个，合计 16,905 行
- 审核方式：机械抽取候选清单 → 逐条对代码确证 → 可实测处用探针实测
- 本轮**只出报告，未改动任何代码**

## 基线（审核前复跑，全绿）

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端测试 | `python -m pytest tests -q` | 149 passed |
| 后端 lint | `ruff check backend/ tests/ setup.py tools/` | All checks passed |
| 前端类型 | `npx tsc -b` | 通过 |
| 前端 lint | `npx eslint .` | 0 error（3 warning） |
| 前端测试 | `npx vitest run` | 7 文件 39 passed |

所以下面的问题都是**测试抓不到的**。

---

## 一、已确证缺陷

### P1-1 控制台会话创建失败会把 `_CREATING` 哨兵永久留在字典里，该主机的控制台从此不可用

`backend/console/session_manager.py:210`

```python
session = self._create(config)      # 抛异常 -> 下面的清理整段被跳过
with self._lock:
    if self._sessions.get(key) is self._CREATING:
        if session is not None:
            self._sessions[key] = session
            return session, True
        del self._sessions[key]     # 只有正常返回 None 才会走到这里
        return None, False
```

`_create` 只把 `build_ssh_client` 包进了 `try`（`:229-237`），**后面的三步没有任何保护**：

```python
channel = ssh.invoke_shell(term="xterm-256color", width=200, height=50)  # 无 try
channel.setblocking(0)                                                   # 无 try
session = ConsoleSession(ssh, channel)
session.start_reader()                                                   # 无 try
```

`get_or_create` 也没有 `try/finally`。于是 `invoke_shell` 一抛，异常直接穿出去，`_sessions[key]` 永远是 `_CREATING`。

**后果链（逐条已核对代码）**

1. `_purge_stale` 显式跳过哨兵（`:154` `if v is not self._CREATING`）→ **永不被回收**。
2. 之后每次连该主机：`creating=True` → 忙等 `_CREATE_WAIT_TIMEOUT` 到 deadline → 返回 `(None, False)`。控制台对该主机**永久不可用**，且每次尝试白占一个工作线程约 20 秒。
3. 已建好的 `ssh` 客户端没被 `close()` → TCP 连接 + paramiko transport 线程泄漏。
4. `active_session_count`（`:253`）也跳过哨兵 → **监控数字看不出异常**，界面没有任何信号。
5. 唯一解法：重启容器。

**触发条件不是理论上的**：`invoke_shell` 在「SSH 认证成功但拒绝开 shell 通道」时抛异常，典型就是 `MaxSessions`（OpenSSH 默认 10）耗尽——而通道泄漏恰恰会耗尽它，**故障会自我维持**。远端 `rbash`、PTY 分配被拒、`sshd` 半死同样命中。

**修法方向**：`get_or_create` 用 `try/except/finally` 包住 `_create`，异常路径必须 `del self._sessions[key]`；`_create` 内 `build_ssh_client` 成功之后的任何失败都要先 `ssh.close()` 再抛。

---

### P2-1 密钥文件缺失/解密失败时，下一次保存配置会把凭据**永久清空**（把可恢复状态变成不可恢复）

`backend/config/remote_client.py:98-117` 与 `:213-232`

解密失败时返回空串：

```python
def _migrate_legacy_encrypted(stored: str) -> str:
    ...
    if not os.path.exists(key_file):
        logger.warning("...密码已清空，请重新设置", key_file)
        return ""                      # 内存里变成空串
    except Exception as e:
        logger.warning("旧版加密密码解密失败（密码已清空，请重新设置）: %s", e)
        return ""
```

`_load_config` 把它写回内存（`:174-178`），而 `_save_config` 的加密循环是：

```python
for field in _CRED_FIELDS:
    value = config_dict.get(field)
    if isinstance(value, str) and value:      # 空串被跳过，不加密
        config_dict[field] = _encrypt_credential(fernet, value)
```

空串被跳过 → **原样落盘**，覆盖掉磁盘上原本还在的密文。

**为什么这是数据损坏而不是「提示用户重设」**：密文本身没坏，只是暂时解不开（`data/.secret_key` 没挂载上、容器重建后卷还没就绪、一次读盘失败）。此时磁盘上的密文是**可恢复的**——把卷挂回来就好了。但只要用户在期间改过一次服务器配置（改 host、改端口都会触发保存），密文就被空串覆盖，**从可恢复变成永久丢失**。

叠加风险：`_fernet()`（`:120-138`）在密钥文件不存在时**直接生成一把新密钥**。密钥一旦丢失，第一次保存就用新钥加密，旧密文从此再也解不开——而这一切只有一行 warning 日志。

**修法方向**：解密失败时不要返回 `""`，而是保留原始密文并置一个「凭据不可用」标记，让保存路径拒绝覆盖这些字段（与 `_save_config` 里「加密失败就中止保存、绝不落明文」的既有取向一致——那里做对了，这里漏了）。

---

### P2-2 下载状态判定：`complete_marker` 优先于 `has_fatal_error`，失败的下载会被报成「完成」

`backend/service/model_service.py:550` 与 `:606-620`

判定顺序是：`is_running` → **`complete_marker`** → `has_fatal_error`。

```python
complete_marker = bool(re.search(r'/models--[\w.()-]+/snapshots/[\w]+', log_content))
...
if complete_marker:
    return {"status": "complete", ..., "message": "Download complete"}   # 先返回

if has_fatal_error:      # 永远轮不到
    ...
    return {"status": "failed", ...}
```

而 `has_fatal_error` 的识别条件里明确包含 `OSError` / `Traceback` / `Exception`——**huggingface_hub 的失败信息本身就带缓存快照路径**，典型如 `OSError: Consistency check failed: ... /models--org--repo/snapshots/<sha>/model.safetensors`。这条日志同时满足「含快照路径」和「含 OSError」，于是被判成 `complete`。

用户看到「下载完成」，但 `scan_models` 扫不到完整模型。

**证据强度说明**：判定顺序与正则匹配是我直接读代码确证的；「HF 失败信息里带缓存路径」这一条来自 huggingface_hub 的已知错误格式，**我没有跑真实失败下载去端到端复现**（需要外网与 HF 账号）。但无论错误串长什么样，「完成标记优先于致命错误」这个顺序本身就是反的——致命错误应当先判。

**修法方向**：把 `has_fatal_error` 提到 `complete_marker` 之前；或要求完成标记同时满足「日志末行是路径且退出正常」。

---

## 二、已排除的指控（查了，不是问题）

对抗审核里同样重要的是不虚报。以下几条是转录里被提出、经我确证后**否决**的：

| 指控 | 否决依据 |
|---|---|
| `POST /scan` 的 `model_dir` 远程命令注入（P0） | `model_ops.py:58` 有 `re.fullmatch(r'[\w./\-:@~]*', _rel)` 白名单，且各分支最终都 `shlex.quote`；`_expand_scan_root` 同样 quote。**无注入面** |
| `get_download_size` / `stop_download` 的 `repo`/`save_path` 注入 | `model_ops.py:300-301` 两者均 `shlex.quote`；`stop_download:327` 同 |
| `build_ssh_client` 的 `connect()` 无超时 → 永久阻塞并永不释放 `conn_lock` | **实测否决**：本机 paramiko 5.0.0 对「TCP 可连但永不发 banner」的服务器，`connect()` 在 **15.1 秒**抛 `SSHException: Error reading SSH protocol banner`（`_check_banner` 自带 15s 上限）。banner 阶段不会永久阻塞 |
| `delete_model` 路径穿越可 `rm -rf` 到别处 | `_assert_deletable` 对 `normalized`、`real_path`、`delete_target` **三个值分别校验**；前缀白名单 + 深度检查；符号链接解析到白名单外会被拒。`_model_delete_target` 上卷到 `/opt/models` 时 `startswith("/opt/models/")` 为假 → 拒绝 |
| 前后端路由契约漂移 | 机械集合差：后端 47 条路由 vs 前端 42 条请求，**前端打到不存在路由的数量为 0**。后端 5 条前端从不调用，均为预期内（`/{full_path:path}` SPA 回退、`/metrics` Prometheus、`/models/{model_id}` 路径参数变体、`/config/server/status`、`/models/ensure-hf-mirror`） |
| `_flush_state` 保存重试导致定时器堆积 | 所有路径都在 `self._lock` 内串行；`_schedule_save` 会 `cancel()` 前一个。仅存在「已触发但阻塞在锁上的旧定时器被孤立」的窄竞态，后果是多一次写入，非缺陷 |

---

## 三、本轮覆盖范围与缺口（重要）

**必须说明：这不是一次完整的全栈审核。** 原计划 14 个领地 × 多 agent 对抗扇出，但本机模型服务吞吐不足，3 轮共 34 个 agent 全部在 10 分钟硬上限上超时（详见下节），我改为「回收转录分析 + 逐条人工确证」，只来得及把**后端 SSH/凭据/状态机/控制台**这条线做到「逐条对代码确证」的强度。

已确证覆盖：`console/session_manager.py`、`config/remote_client.py`、`executor/remote/ssh.py`、`executor/remote/model_ops.py`、`service/model_service.py`（下载状态、删除路径、启动探针）、`controller/state_machine.py`、`api/models.py`、路由契约。

**未做有效覆盖（转录里有线索但未经我确证，不要当作已审）**：

- `service/deployment_service.py`（1344 行，最大文件；`sudo` 密码文件 `/tmp/.vdb_pw_*` 的生命周期、`nohup bash -c "{escaped}"` 双重转义、任务状态机与重启后卡 running——转录里有未完成的分析）
- 前端全部：hooks 的轮询竞态与卸载后 setState、xterm 生命周期、`useBenchmark` 两处裸 `JSON.parse`、`api_key` 走 query 参数进浏览器历史/Referer
- `monitor/`、`ws/hardware_broadcaster.py`、`api/hardware.py`
- 测试有效性与 CI（CI 用 `unittest discover` 而本地用 pytest、CI py3.12 vs 本机 3.14、Dockerfile）
- `middleware.py` 鉴权绕过的系统性验证（只确认了 `hmac.compare_digest` 用法正确）

## 四、审核工具链本身的结论（避免下次重犯）

本轮 3 次 workflow 扇出共 34 个 agent 全部超时，根因**不是并发数**：

- 并发 8 与并发 4 都是全灭；前中期回合只要 0–70 秒
- `usageMetadata` 显示**最后一轮输出 2–3 万 token**（`find:B1-deploy` 末轮 31,614 / 全程 32,076），即几乎全部输出都堆在被砍掉的那一轮
- 结论：在本机模型吞吐下，「读一堆文件 → 一次性输出大量结构化结论」这种 agent 形态无法在 10 分钟墙钟内完成

下次做同类审核应当：**限制单个 agent 的输出规模**（≤5 条、每条精简），并把范围切到「判定一份预先抽好的候选清单」而非自由漫游。机械抽取（路由、sink、凭据触点、effect 清单）由主循环做，成本极低且无超时问题——本轮的 `audit_scan.txt` 就是这么产出的，也确实由此否决了 3 条 P0 误报。

---

## 建议处理顺序

1. **P1-1** 控制台 `_CREATING` 泄漏——加 `try/finally` 清理 + `_create` 后半段失败时 `ssh.close()`。改动小、收益明确。
2. **P2-1** 凭据被静默清空——解密失败保留密文并拒绝覆盖，复用 `_save_config` 已有的「宁可不存」取向。
3. **P2-2** 下载状态判定顺序——把致命错误提到完成标记之前。
4. 补 `service/deployment_service.py` 与前端的专项审核（本轮缺口最大处）。
