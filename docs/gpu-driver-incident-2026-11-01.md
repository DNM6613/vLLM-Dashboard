# GPU 主机（10.131.1.7）NVML 驱动不匹配事故报告

**日期**：2026-11-01　**现象**：`nvidia-smi` 报 `Failed to initialize NVML: Driver/library version mismatch`；vLLM 启动时 torch 报 `Can't initialize NVML`（`torch/cuda/__init__.py:1112`）。**状态：未做任何修复操作，仅只读诊断。**

## 结论

**10-31 17:22 有人在该机运行 NVIDIA 官方 `.run` 安装器安装 615.78.08，覆盖了 apt 包管理体系的用户态驱动库并放入了自己的内核模块；而系统 apt/DKMS 世界仍是 615.71.09。开机时 initramfs 携带的旧版 615.71.09 内核模块先加载，与 615.78.08 用户态库不匹配，导致 NVML 初始化失败。今天 11:53 的重启无效，正是因为 initramfs 里背的还是旧模块。**

## 证据链

| 项 | 实测值 | 说明 |
|---|---|---|
| 内核已加载模块 | 615.71.09 | `/proc/driver/nvidia/version` |
| 磁盘内核模块（modinfo -n） | 615.78.08 | `/lib/modules/7.0.0-42-generic/kernel/drivers/video/nvidia.ko`（`.run` 放置，mtime 10-31 17:17） |
| 用户态 NVML / CUDA 库 | 615.78.08 | `libnvidia-ml.so.615.78.08`、`libcuda.so.615.78.08`（mtime 10-31 17:22，**dpkg -S 查无所属** = 非 apt 安装） |
| apt 包版本（driver/dkms/utils/compute 全套） | 615.71.09-0ubuntu0.26.04.2 | apt 候选也是 615.71.09，**apt 装不出 615.78** |
| DKMS | nvidia/615.71.09 built | `updates/dkms/` 下为 DKMS 模块 |
| initramfs（7.0.0-42-generic） | 含 `updates/dkms/nvidia.ko.zst` = 615.71.09 | 开机先加载它 → 与 615.78 用户态错位 |
| `.run` 安装日志 | `/var/log/nvidia-installer.log`，10-31 17:22 "Installation of ... 615.78.08 is now complete" | 铁证 |
| 最近重启 | 2026-11-01 11:53:00 | 重启后故障依旧（initramfs 未变） |
| 卸载器 | `/usr/bin/nvidia-uninstall` 存在（指向 nvidia-installer） | `.run` 卸载通道可用 |

注：`.run` 操作不在 uforce 的 bash history 中（可能经 root 或其他途径执行）；当天 06:30 另有一轮 `unattended-upgrade`，但与 615.78.08 无关（候选版本不含此版）。

## 修复方案（二选一，均已拟定、未执行）

### 方案 A：恢复纯 apt 管理的 615.71.09（推荐）

关键事实：**当前内核里加载的就是 615.71.09**，只需把用户态库恢复成同版本，`nvidia-smi` 立即恢复、vLLM 可马上拉起，**修复本身不需要重启**；重启仅用于固化 initramfs、防止下次开机复发，可择期执行。

```bash
# 1) 卸载 .run 残留（用户态库 + kernel/drivers/video/ 下的 615.78.08 模块）
sudo nvidia-uninstall
# 2) 恢复被覆盖的 615.71.09 用户态文件
sudo apt-get install --reinstall libnvidia-compute-615-server libnvidia-cfg1-615-server libnvidia-common-615-server nvidia-utils-615-server
# 3) 重建模块依赖与 initramfs
sudo depmod -a && sudo update-initramfs -u
# 4) 验证（此时不重启即应通过）
nvidia-smi
# 5) 择期重启一次，固化 initramfs，之后重启永远安全
sudo reboot
```

优点：回到纯包管理，根治；本次事故的根源（混装）被清除。缺点：步骤稍多。

### 方案 B：对齐到 .run 的 615.78.08

```bash
sudo depmod -a          # 确认 modprobe 解析到 kernel/drivers/video/nvidia.ko (615.78.08)
sudo update-initramfs -u
sudo reboot             # 必须重启，加载新模块
```

优点：命令最少。缺点：**runfile 与 apt/DKMS 混装保留**——runfile 模块不经 DKMS 管理，下次换内核不会自动重建（换内核即挂）；apt 侧驱动更新时大概率复发本次事故。

## 共同注意项

- 修复动作含 sudo 与（方案 B / 择期固化时）一次重启，会中断该机所有 GPU 任务。
- 修复后验证：`nvidia-smi` 正常 → vLLM 服务拉起 → dashboard 三灯与 GPU 卡片恢复。
- 建议补一条运维约束：该机驱动只经 apt/DKMS 安装（`nvidia-driver-615-server-open`），禁用 `.run` 安装器。
