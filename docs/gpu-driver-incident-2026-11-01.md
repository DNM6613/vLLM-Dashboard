# GPU 主机（10.131.1.7）NVML 驱动不匹配事故报告

**日期**：2026-11-01　**现象**：`nvidia-smi` 报 `Failed to initialize NVML: Driver/library version mismatch`；vLLM 启动时 torch 报 `Can't initialize NVML`（`torch/cuda/__init__.py:1112`）。

**状态：已恢复（2026-11-01 12:12）**——DKMS 615.71.09 彻底注销 + 重启后，内核/用户态均为 615.78.08（自编译 P2P 内核模块 + .run 用户态），nvidia-smi 正常、P2P 4x4 全 OK、vLLM healthy（11-01 晚间复核确认）。**本报告初版的「修复方案 A/B」基于误判，整体作废，方案 A 严禁执行**（见下）。

## 更正（2026-11-01 晚间复核）

初版「结论」把 10-31 17:22 的 .run 安装误判为「有人」的异常操作。实际：**那是 uforce 自己发起的计划性 P2P cutover（`~/p2p-install/phase2_cutover.sh`）**——本机驱动自 10-31 起有意切到「自编译 615.78.08-p2p 内核模块 + .run 615.78.08 用户态（`--no-kernel-modules`）」，**不走 apt/DKMS**（`~/p2p-install/NOTES.txt` 明确「重要: 本机驱动不走 apt/DKMS!」）。.run 不在 bash history 中是因为它由 cutover 脚本在内部调用。

初版方案 A（恢复纯 apt 615.71.09）会摧毁该配置：`nvidia-uninstall` 移除当前在役的 615.78.08 用户态，用户态降回 615.71.09 后与自编译 615.78.08-p2p 内核模块再次失配，且 P2P 补丁体系整体失效（PCIe P2P 速率受限）。**方案 A 作废，勿执行。**

## 结论（修订）

事故**真正触发点**：11-01 06:30 `unattended-upgrade` 重建了 apt 侧 DKMS 615.71.09 模块（`updates/dkms/`），依 depmod "updates built-in" 优先级**遮蔽**了 `kernel/drivers/video/` 的自编译 615.78.08-p2p 模块；11:53 重启后内核加载 615.71.09，与 615.78.08 用户态失配 → NVML 初始化失败。12:12 `dkms remove nvidia/615.71.09 --all`（DKMS 彻底注销、`updates/dkms` 清空）+ 重启后恢复。

## 时间线（全程证据）

| 时间 | 事件 |
|---|---|
| 10-26 21:38 | 克隆 open-gpu-kernel-modules 到 `~/p2p-install/src-p2p`，产出 P2P 补丁件（`patch-libcuda-p2p.py` 等） |
| 10-31 17:04 | 由 src-p2p 编译 P2P 补丁内核模块 615.78.08（`uforce@ai-server` 签名） |
| 10-31 17:13 | 下载 `NVIDIA-Linux-x86_64-615.78.08.run`（529834675 字节） |
| 10-31 17:16–17:36 | `phase2_cutover.sh`：抓取并停 vLLM（argv/env 存 bin）→ `.run --no-kernel-modules`（用户态 615.78.08）→ rmmod 旧模块 → `dkms remove nvidia/615.71.09 --all` → `make modules_install`（自编译模块 → `kernel/drivers/video/`，17:17:12）→ 中途一次 nvidia-smi 校验失败自动 rollback 后重走 → **`CUTOVER_STATUS: OK @ 2026-10-31_17:36:03 KMD=615.78.08-p2p P2P=OK vLLM=healthy`** |
| 11-01 06:30 | `unattended-upgrade` 重建 DKMS 615.71.09（`updates/dkms/`），遮蔽自编译模块（**事故根因动作**） |
| 11-01 11:53 | 重启 → 内核 615.71.09 + 用户态 615.78.08 → NVML 失配（初版报告诊断点） |
| 11-01 12:12 | DKMS 615.71.09 彻底注销 + 重启 → 内核 = 615.78.08-p2p（自编译），与用户态一致 → vLLM 恢复（boot.log attempt 12:12:13） |
| 11-01 20:16 | 再次重启（vLLM 随后 healthy，pid 117325 在跑） |
| 11-01 晚间 | 复核：`nvidia-smi -L` 4× RTX 5060 Ti 正常；`topo -m` 相邻卡 PHB P2P；`modinfo -n nvidia` = `kernel/drivers/video/nvidia.ko`（615.78.08） |

## 诊断点（11-01 中午）时点证据，存档

| 项 | 实测值 | 说明 |
|---|---|---|
| 内核已加载模块 | 615.71.09 | `/proc/driver/nvidia/version`（重启后由 unattended-upgrade 重建的 DKMS 模块加载） |
| 磁盘内核模块（modinfo -n） | 615.78.08 | `/lib/modules/7.0.0-42-generic/kernel/drivers/video/nvidia.ko`（**cutover 的 make modules_install 于 17:17 放入，非 .run 所为**；.run 用 `--no-kernel-modules`） |
| 用户态 NVML / CUDA 库 | 615.78.08 | `libnvidia-ml.so.615.78.08`、`libcuda.so.615.78.08`（mtime 10-31 17:22，dpkg -S 查无所属 = .run 安装，计划内） |
| apt 包版本（7 包） | 615.71.09-0ubuntu0.26.04.2 | apt 记账与磁盘实际脱节（计划内）；现已全部 `apt-mark hold` |
| DKMS（诊断点） | nvidia/615.71.09 built | 06:30 unattended-upgrade 重建；**12:12 已彻底注销，现为空** |
| initramfs（诊断点） | 含 updates/dkms/nvidia.ko.zst = 615.71.09 | 11:53 重启加载旧模块的原因；注销 DKMS 后不再复发 |
| .run 安装日志 | 10-31 17:22 "Installation of ... 615.78.08 is now complete" | 仅用户态（`--no-kernel-modules` 警告在案） |
| 卸载器 | `/usr/bin/nvidia-uninstall` 存在 | **勿用**（会移除在役 615.78.08 用户态） |

## 根因机制（对照 open-gpu-kernel-modules 仓库与 NVIDIA 官方文档）

1. **两条渠道的内核模块同源**：NVIDIA 内核模块开源（MIT/GPLv2 双许可），每个驱动版本随附源码发布于 `NVIDIA/open-gpu-kernel-modules` 仓库；570 起专有内核模块已移除，open 为唯一形态。DKMS 615.71.09 与自编译 615.78.08-p2p 是同一源码树的两个版本（后者含本地 P2P 补丁）。
2. **内核/用户态版本严格绑定**：仓库 README 明确内核模块 "must be used with GSP firmware and user-space NVIDIA GPU driver components from a corresponding 615.78.08 driver release"。NVML 版本校验在内核与用户态版本不一致时硬错误（`Driver/library version mismatch`；torch 的 NVML 初始化同理）。
3. **depmod 优先级是事故的放大器**：.run/自编译模块在 `kernel/drivers/video/`（built-in 内核目录），DKMS 模块在 `updates/dkms/`。depmod 默认搜索顺序 "updates built-in"（depmod.d(5)）——`updates/` 恒优先，同名模块先列者遮蔽后列者。unattended-upgrade 一旦重建 DKMS 模块，自编译模块立刻被遮蔽，重启后加载错版本。
4. **本机的双渠道是有意设计而非意外混装**：自编译内核模块 + .run 用户态（`--no-kernel-modules`）是 open-gpu-kernel-modules README 官方支持的组合（"This can be achieved by installing the NVIDIA GPU driver from the .run file using the `--no-kernel-modules` option"）。风险点不在 .run 本身，而在 **apt 侧自动升级（unattended-upgrade）重新植入 DKMS 模块**打破 depmod 解析——11-01 06:30 即由此复发。

## 方案作废与现行运维基线

- **方案 A（恢复 apt 615.71.09）：作废，勿执行**——方向与本机既定运维策略相反（见「更正」）。
- 原方案 B 的 `dkms remove nvidia/615.71.09 --all` 前置步骤即 12:12 已完成的动作，现状即其结果。
- 现行基线（权威说明以 `~/p2p-install/NOTES.txt` 为准，2026-11-01 更新）：
  1. **驱动不走 apt/DKMS**：自编译 615.78.08-p2p 模块（`kernel/drivers/video/`）+ .run 615.78.08 用户态；DKMS 615.71.09 已注销；7 个 nvidia apt 包 `apt-mark hold`（libnvidia-cfg1/common/compute-615-server、nvidia-dkms-615-server、nvidia-dkms-615-server-open、nvidia-driver-615-server-open、nvidia-utils-615-server），防止 unattended-upgrade 复发。
  2. **P2P**：`/etc/modprobe.d/nvidia.conf` 注入 `NVreg_RegistryDwords="RMForceStaticBar1=1;RMPcieP2PType=1;RMForceP2PType=1"`（static BAR1 + PCIe P2P）；vLLM 用 `NCCL_P2P_LEVEL=PHB`（实测 154 tok/s 最优，NODE 反而 140，勿改）。
  3. **内核升级时**：自编译模块会丢失 → `cd ~/p2p-install/src-p2p && make modules -j32 && sudo make modules_install && sudo depmod -a $(uname -r)`。
  4. **重启后若再遇 NVML 失配**：`head -2 /proc/driver/nvidia/version` 看内存里加载的版本；`modinfo -n nvidia` 若未解析到 `kernel/drivers/video/nvidia.ko`（615.78.08）→ `sudo depmod -a`，然后 `sudo systemctl stop nvidia-persistenced` + `rmmod nvidia_uvm nvidia_drm nvidia_modeset nvidia` + `modprobe nvidia nvidia_modeset nvidia_uvm nvidia_drm`，再 `bash /tmp/vllm_boot.sh` 重启 vLLM（/tmp 已清时用 `~/p2p-install/vllm_argv.bin` + `vllm_env.bin` 重放）。
  5. 解除 hold 须另行评估（`sudo apt-mark unhold <包名>`）。
- 残留小项：`/lib/firmware/nvidia/615.71.09` 固件残留（无害）；**libcuda 侧 P2P 补丁未施加**（2026-11-01 晚间逐点核验：系统 `libcuda.so.615.78.08` 中 `patch-libcuda-p2p.py` 的 4 个签名点均为原始 `je`（0x74），未打签名各命中 1 次 @ 0x31ee44/0x31f050/0x4ba723/0x31ef49，打后签名 0 次；mtime = .run 落盘时刻 17:22:03，cutover 脚本与日志均无调用痕迹）——当前 P2P 由内核侧（自编译模块 + dwords）单独承载，`topo -p2p rw` OK；若将来 CUDA 运行时层 P2P 门控出现异常，先备份再施加：`cp -a /usr/lib/x86_64-linux-gnu/libcuda.so.615.78.08{,.unpatched}` + `sudo python3 ~/p2p-install/src-p2p/patch-libcuda-p2p.py /usr/lib/x86_64-linux-gnu/libcuda.so.615.78.08`（脚本自带单次匹配校验，已打过的文件自动拒绝重打）。
