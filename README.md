# IRIS — IoT Rehosting & Interconnection Simulator（鸢尾）

面向路由器、IP 摄像头等网络设备的全自动化固件仿真（Rehosting）平台。
为每台真实网络设备打造可交互的"虚拟替身"：固件包输入，网络可达的仿真设备输出，全程无人值守、失败可归因。

> 命名寓意：**I**oT **R**ehosting & **I**nterconnection **S**imulator；相机光圈（摄像头场景）、虹膜（看清设备内部）。

## 定位速览

| 维度 | 内容 |
|---|---|
| 主干路线 | QEMU 全系统仿真 + 定制内核插桩 + libnvram 用户态仿真（FirmAE 已验证路线） |
| arm64 通道 | Alpine generic virt 内核 + 自建 initramfs，直跑厂商 `/sbin/init`（FirmAE 无 aarch64 内核的补位方案） |
| 差异化 | 结构化失败画像 + 规则引擎/LLM 双轨环境恢复；摄像头媒体面（RTSP/ONVIF）；多设备虚拟组网 |
| 量化目标 | 精选评测集 Web 可达 ≥80%；长尾语料 ≥60%（对标 FirmPilot 2026 的 52.39%） |

## 当前能力

| 层 | 能力 | 状态 |
|---|---|---|
| L1 提取 | 格式识别（TendaW / squashfs / JFFS2 / uImage / UBI / FIT / 加密厂商格式）、rootfs 解包、ELF 架构校验入库 | ✅ |
| L2 仿真 | QEMU 全系统仿真，四架构通道：`mipsel` / `mipseb` / `armel` / `arm64`；架构预检（不符直接给出正确架构建议，`--force` 可绕过）；Docker 网络桥接 + 主机端口转发 + 串口日志采集 | ✅ |
| L3 规则 | 最小 YAML 启动修复规则引擎（`rules/`），可插拔、可回归 | ✅ |
| L4 交互 | RTSP/ONVIF 媒体面 | 🔬 M3 |
| L5 编排 | Typer CLI + FastAPI 服务（上传固件 → 提取 → 仿真一条 `/api/v1/pipeline` 打通） | ✅ |

## 快速开始

```bash
# 1) 安装（Python 3.11+，需要 Docker；详见 docs/04-快速部署.md）
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -e ".[dev]"

# 2) 初始化数据库 + 构建仿真镜像（首次约 5~10 分钟）
iris db init
docker build -t iris-emulate:latest         -f docker/emulate/Dockerfile .
docker build -t iris-emulate-baked:latest   -f docker/emulate/Dockerfile.baked .

# 3) 分析一个固件包：识别格式/架构，提取 rootfs
iris extract inspect "path/to/firmware.bin"
iris extract add      "path/to/firmware.bin"      # 入库（ELF 架构校验）
iris extract rootfs   "path/to/firmware.bin" --out ./rootfs_out

# 4) 仿真并检查 Web 可达（端口转发到主机 8080）
iris emulate run ./rootfs_out --arch mipsel --port 8080 --timeout 180
# aarch64 rootfs：arm64 通道整机起壳慢，超时给到 480s 以上
iris emulate run ./rootfs_out --arch arm64 --port 8090 --timeout 480
# 浏览器访问 http://127.0.0.1:8080

# 5) 或走 HTTP API
iris serve start                                   # docs: http://127.0.0.1:9000/docs
curl -F "file=@firmware.bin" http://127.0.0.1:9000/api/v1/pipeline
```

`iris emulate run` 的输出即"失败可归因"的入口：`success / web ok / web url / duration / error / 串口日志尾部`；
完整日志在 `iris-home/scratch/<iid>/qemu.serial.log`，排查顺序见 docs/04-快速部署.md 第 7 节故障速查表。

## 已验证样例（M0/M1 评测集）

| 固件 | arch | 结果 |
|---|---|---|
| OpenWrt Archer C7 v2 | mipseb | ✅ Web 可达（HTTP 200，73s） |
| OpenWrt Newifi D2 | mipsel | ✅ Web 可达（HTTP 200，47s） |
| D-Link DIR-868L revB | armel | ⚠️ 服务起、VLAN 路由不通 |
| Linksys WRT1200AC / Netgear R7800 | armel | ⚠️ 启动后 kernel panic（nlattr） |
| Tenda TC3T14C（摄像头） | armel | 多 JFFS2 合并提取成功，Lua init 仿真待研究 |
| Tenda US_i29 / TES7002（aarch64） | arm64 | ✅ TES7002 走 arm64 通道：厂商 init + `iris_net_fix` 兜底网络，Web 登录页 HTTP 200（两次实测 289s / 258s，`--timeout` 需 ≥480）；US_i29 仍需 arm64 内核镜像 |
| YZTenda 加密固件 | — | 明确失败画像："FIT + 加密无法提取"（不再误报 no squashfs） |

明细与日志指纹见 `docs/eval-log.md`。

## 目录结构（Monorepo）

```
IRIS/
├── docs/             # 开发规划、技术栈、快速部署、评测日志
├── src/iris/         # 主代码（L1 提取 / L2 仿真 / L3 规则 / L5 CLI+API）
├── kernel/           # Linux 内核 fork 补丁与构建脚本（定制插桩内核）
├── libnvram/         # NVRAM 用户态仿真库（C，-nostdlib）
├── rules/            # 修补策略库（YAML 规则，可插拔可回归）
├── scripts/emulate/  # L2 运行脚本：make_image / run_qemu / iris_net_fix /
│                     # arm64 initramfs 构建与资产下载（get_arm64_assets.py）
├── docker/emulate/   # iris-emulate（基础）与 iris-emulate-baked（脚本+资产烘焙）
├── binaries/         # 内核镜像、console、libnvram、arm64 initramfs 等资产（含 sha256 清单）
├── tools/            # QEMU fork 管理、镜像构建等辅助脚本
└── tests/            # 单元测试与评测集回归
```

## 里程碑（详见 docs/01-开发规划.md）

M0 技术验证 → M1 MVP 主干 → M2 规模化 → M3 交互与分析 → M4 智能环境恢复 → M5 产品化

## 已知限制

- **arm64 通道无 libnvram / console 劫持**：FirmAE 未发布 aarch64 插桩内核，依赖 nvram 的厂商服务在 arm64 下起不来（属通道设计取舍，非缺陷）；
- **厂商网络模型强绑定真实存储**：如 Tenda configd 依赖 `ubi0:ubi_Config` 挂载 `/var/config`，仿真环境无该分区时 eth0 不获址，由 `iris_net_fix` 兜底（补 IP、放行 iptables、telnetd:7002、goahead/boa Web 拉起），TES7002 实测由此拿到可达 Web；
- **QEMU CPU 型号**：厂商 aarch64 二进制常用 ARMv8.3 指针认证（PAC），arm64 通道必须 `-cpu max`，否则 SIGILL；
- x86 语料不在仿真范围（当前仅 mipsel/mipseb/armel/arm64）。

## 协作规范（强制）

1. **每个任务推进必须建立本地 Git commit 节点**，以便随时回滚；遵循 Conventional Commits（`feat:` / `fix:` / `docs:` / `chore:` / `refactor:` / `test:`）；
2. 阶段性成果用 tag 标注里程碑（如 `m1-mvp`）；
3. 大型改动拆分为多个可独立回滚的 commit，禁止一次性巨型提交。
