# IRIS — IoT Rehosting & Interconnection Simulator（鸢尾）

面向路由器、IP 摄像头等网络设备的全自动化固件仿真（Rehosting）平台。
为每台真实网络设备打造可交互的"虚拟替身"：固件包输入，网络可达的仿真设备输出，全程无人值守、失败可归因。

> 命名寓意：**I**oT **R**ehosting & **I**nterconnection **S**imulator；相机光圈（摄像头场景）、虹膜（看清设备内部）。

## 定位速览

| 维度 | 内容 |
|---|---|
| 主干路线 | QEMU 全系统仿真 + 定制内核插桩 + libnvram 用户态仿真（FirmAE 已验证路线） |
| 差异化 | 结构化失败画像 + 规则引擎/LLM 双轨环境恢复；摄像头媒体面（RTSP/ONVIF）；多设备虚拟组网 |
| 量化目标 | 精选评测集 Web 可达 ≥80%；长尾语料 ≥60%（对标 FirmPilot 2026 的 52.39%） |

## 目录结构（Monorepo）

```
IRIS/
├── docs/        # 开发规划、技术栈、架构决策记录
├── src/iris/    # 主代码（Python 包：L1 提取 / L3 环境恢复 / L4 交互 / L5 编排）
├── kernel/      # Linux 内核 fork 补丁与构建脚本（L2）
├── libnvram/    # NVRAM 用户态仿真库（C，-nostdlib）
├── rules/       # 修补策略库（YAML 规则，可插拔可回归）
├── tools/       # QEMU fork 管理、镜像构建等辅助脚本
└── tests/       # 单元测试与评测集回归
```

## 里程碑（详见 docs/01-开发规划.md）

M0 技术验证 → M1 MVP 主干 → M2 规模化 → M3 交互与分析 → M4 智能环境恢复 → M5 产品化

## 协作规范（强制）

1. **每个任务推进必须建立本地 Git commit 节点**，以便随时回滚；遵循 Conventional Commits（`feat:` / `fix:` / `docs:` / `chore:` / `refactor:` / `test:`）；
2. 阶段性成果用 tag 标注里程碑（如 `m1-mvp`）；
3. 大型改动拆分为多个可独立回滚的 commit，禁止一次性巨型提交。