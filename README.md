# IRIS — IoT Rehosting & Interconnection Simulator（鸢尾）

面向路由器、IP 摄像头等网络设备的全自动化固件仿真（Rehosting）平台。
为每台真实网络设备打造可交互的"虚拟替身"：固件包输入，网络可达的仿真设备输出，全程无人值守、失败可归因。

> 命名寓意：**I**oT **R**ehosting & **I**nterconnection **S**imulator；相机光圈（摄像头场景）、虹膜（看清设备内部）。

## 定位速览

| 维度 | 内容 |
|---|---|
| 主干路线 | QEMU 全系统仿真 + 定制内核插桩 + libnvram 用户态仿真（FirmAE 已验证路线） |
| arm64 通道 | Alpine generic virt 内核 + 自建 initramfs，直跑厂商 `/sbin/init`（FirmAE 无 aarch64 内核的补位方案） |
| 差异化 | 结构化失败画像 + 可插拔规则引擎（YAML，零 Python）；摄像头媒体面（RTSP/ONVIF，规划中）；多设备虚拟组网（规划中） |
| 当前实测 | 同语料 5 设备 Web 可达 **3/5**（`docs/eval-log.md` M1 表，逐条附日志指纹）。两个未通过均为宿主内核 `validate_nla` BUG，已定位、项目内不可修 |
| 目标 | 精选评测集 Web 可达 ≥80%；长尾语料 ≥60%（对标 FirmPilot 2026 的 52.39%）。**尚未达成**，当前 60% |

> 上表的目标行是目标，不是现状。数字全部取自 `docs/eval-log.md` 的实测记录，改动仿真链路后必须同步刷新该表。

## 当前能力

| 层 | 能力 | 状态 |
|---|---|---|
| L1 提取 | 格式识别（TendaW / squashfs / uImage / UBI / FIT magic / 加密厂商格式**仅识别不解密**）、rootfs 解包（squashfs 为唯一解包路径）、ELF 架构校验入库 | ✅ |
| L2 仿真 | QEMU 全系统仿真，四架构通道：`mipsel` / `mipseb` / `armel` / `arm64`；架构预检（不符直接给出正确架构建议，`--force` 可绕过）；Docker 网络桥接 + 主机端口转发 + 串口日志采集 | ✅ |
| L2 诊断 | **链路分层主动探测**：失败路径上实测 route / ARP / ICMP / service 四层，产出链路分层表并直接命名断点所在层（`link-no-route` / `link-no-arp` / `link-no-icmp` / `link-no-service`） | ✅ |
| L3 规则 | YAML 启动修复规则引擎（`rules/`，6 条实证规则），可插拔、可回归，修复后带证据校验 | ✅ |
| L4 交互 | RTSP/ONVIF 媒体面 | 🔬 未实现（M3 规划） |
| L5 编排 | Typer CLI + FastAPI 服务（上传固件 → 提取 → 仿真一条 `/api/v1/pipeline` 打通）+ 值守监控（`emulate guardian-start`，规则+状态机，**不含模型调用**） | ✅ |
| L5 交付面 | API 鉴权（`IRIS_API_TOKEN`）、按调用方隔离的仿真归属、上传大小上限、运行状态落库（重启可见） | ✅ |

### 能力边界（请按此判断可行性）

- **LLM 尚未接入**：失败修复当前全部由 YAML 规则引擎完成，仓库内没有任何模型调用代码。`ai_guardian.py` 是正则 + 状态机的规则式值守，不含推理。
- **解包格式单一**：实际可解包的只有 `squashfs`。`ext4` / `cramfs` / `yaffs2` / `cpio` / `tar` 会明确落到 `no-rootfs` 失败画像，而不是静默产出错误 rootfs。`JFFS2` 仅在 TendaW 容器内可解。
- **网络拓扑单平面**：单 TAP + 单网桥 + 固定 VLAN 1，端口转发目标端口硬编码；无 `eth1` 及以上网卡，无无线（802.11）仿真。
- **x86 语料不在仿真范围**。

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
# --out 可选：把 rootfs 提取到指定目录，默认落在 iris-home/scratch/<固件名>-rootfs。
# 目标目录非空时拒绝覆盖（需显式 --force）；squashfs 切片等中间产物始终留在 scratch。
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

### API 交付面

默认**只监听本机**。要让局域网访问，必须显式配 token，否则 `serve start` 会拒绝启动（退出码 2）：

```bash
export IRIS_API_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
iris serve start --host 0.0.0.0 --port 9000
curl -H "Authorization: Bearer $IRIS_API_TOKEN" -F "file=@firmware.bin" \
     http://127.0.0.1:9000/api/v1/pipeline
```

- 除 `/api/v1/health`（探针用，故意免鉴权）外，所有路由都要求 token：`Authorization: Bearer <token>` 或 `X-IRIS-Token` 两种写法均可。
- 仿真按调用方隔离：`GET` / `DELETE /api/v1/emulate/{iid}` 只能操作**自己创建**的仿真，越权一律返回 404（不区分「不存在」与「不属于你」，避免泄露 id 是否存在）。
- 运行中的仿真记录在数据库而不是进程内存里，**重启服务后仍可见**；容器已消失的记录会被自动清理。
- 上传上限 `IRIS_API_MAX_UPLOAD_MB`（默认 64），超出返回 413。

**调试入口 / Debug entrypoint**：根目录 `iris.py` 与 `iris` 命令、`python -m iris.cli` 完全等价，
但它是个真实文件，IDE 的"调试当前文件"可直接打上断点跑通整条 CLI 链路，无需配置 module 与工作目录：

```bash
python iris.py --help
python iris.py rules list
python iris.py emulate run ./rootfs_out --arch mipsel --port 8080
```

> 因项目为 src-layout 且该文件名与包同名 `iris`，`pytest` 已改用 `--import-mode=importlib`
> 并显式声明 `pythonpath = ["src"]`；否则仓库根目录会被插到 `sys.path[0]`，`import iris`
> 会先命中 `iris.py` 而非 `src/iris` 包。

`iris emulate run` 的输出即"失败可归因"的入口：`success / web ok / web url / duration / error / 串口日志尾部`；
完整日志在 `iris-home/scratch/<iid>/qemu.serial.log`，排查顺序见 docs/04-快速部署.md 第 7 节故障速查表。

## 已验证样例（M0/M1 评测集）

下表为 **0.3.12 同批实测**（iid 6711–6715，`--timeout 300/240`），不是历史最优值：

| 固件 | arch | 结果 |
|---|---|---|
| OpenWrt Newifi D2 | mipsel | ✅ Web 可达（HTTP 200，62.2s） |
| OpenWrt Archer C7 v2 | mipseb | ✅ Web 可达（HTTP 200，49.9s） |
| D-Link DIR-868L revB | armel | ✅ Web 可达（HTTP 200，53.0s）— 0.3.12 由失败转成功，见下方说明 |
| Linksys WRT1200AC | armel | ❌ HTTP 000（312.8s）。**环境适配失败**：宿主内核在 `validate_nla+0x3c` 触发 BUG（`udf` 陷阱），netifd 被打死 → eth0 地址丢失。项目内不可修 |
| Netgear R7800 | armel | ❌ HTTP 000（246.0s）。与 WRT1200AC **同一根因**（`pc` 与出错文件行号完全相同） |
| Tenda TC3T14C（摄像头） | armel | 多 JFFS2 合并提取成功，Lua init 仿真待研究 |
| Tenda US_i29 / TES7002（aarch64） | arm64 | ✅ TES7002 走 arm64 通道：厂商 init + `iris_net_fix` 兜底网络，Web 登录页 HTTP 200（两次实测 289s / 258s，`--timeout` 需 ≥480）；US_i29 仍需 arm64 内核镜像 |
| YZTenda 加密固件 | — | 明确失败画像："FIT + 加密无法提取"（不再误报 no squashfs） |

**DIR-868L 的结论曾被推翻两次**，这里记录最终状态以免旧结论再次流传：最初记为「服务起、VLAN 路由不通」，0.3.11 记为「宿主与 guest 不同子网导致全网失败」，两者都被推翻。真实根因在宿主侧——`run_qemu.sh` 把宿主桥放在 `192.168.1.254/16`，而目标网络是 `192.168.0.0/24`，宿主地址不在 guest 子网内被静默丢弃。修复是在宿主桥上补一个落在 guest 子网内的地址（`.254`，guest 占用时退 `.253`）。**这是宿主基础设施缺陷，不是固件缺陷，也不是 VLAN 问题。**

明细与日志指纹见 `docs/eval-log.md`。

### 口径可执行化（`iris corpus eval`）

上表的数字此前靠人手维护：结论被推翻两次，旁边的失败表却没人同步，两个文件能各说各话。
现在判定由 `iris corpus eval` 从语料清单 + 实测记录算出，三种口径分开报：

```bash
# 从 emulation_run 取每个条目最新一次运行来评分（清单里的 db_match 声明对应关系）
python iris.py corpus eval --from-db --env-broken openwrt-wrt1200ac,openwrt-r7800

# 导出报告，并与上一版逐设备对比回归
python iris.py corpus eval --from-db --write-json iris-home/corpus/m0-report.json
python iris.py corpus eval --from-db --baseline iris-home/corpus/m0-report.json \
    --write iris-home/corpus/m0-report.md
```

报告写到 `iris-home/corpus/`，**不要覆盖 `docs/eval-log.md`**：那份文件还带串口日志指纹，
是人工判读的证据；`corpus eval` 的输出口径不同，覆盖它等于删掉证据。

- **Web 可达率**：分母含环境失败条目——用户依然没拿到设备。
- **能力口径**：分母剔除环境失败条目——只衡量宿主健康时 IRIS 能做到什么。
- **逐设备回归**：`3/5 → 4/5` 可能藏着一修一坏，比率相等时尤其如此，所以回归按设备点名。

三条口径都不能靠「全部条目」当分母：清单里没声明期望的条目永远不进分母，
未实测的条目记 `skipped`。`db_match` 声明与 `image` 表 filename 的对应关系，
匹配不上的条目显示为未实测，而不是悄悄缩小分母。

## 目录结构（Monorepo）

```
IRIS/
├── docx/              # 使用与治理文档（免安装使用指南、AI 值守与稳定性治理）
├── docs/              # 开发规划、技术栈、快速部署、崩溃归因、架构修复、评测日志
├── src/iris/          # 主代码（L1 提取 / L2 仿真 / L3 规则 / L4 值守监控 / L5 CLI+API）
├── kernel/            # Linux 内核 fork 补丁与构建脚本（定制插桩内核，规划中）
├── libnvram/          # NVRAM 用户态仿真库（C，-nostdlib，规划中）
├── rules/             # 修补策略库（YAML 规则，可插拔可回归）
├── scripts/emulate/   # L2 运行脚本：make_image / run_qemu / iris_net_fix /
│                     # arm64 initramfs 构建与资产下载（get_arm64_assets.py）
├── docker/emulate/   # iris-emulate（基础）与 iris-emulate-baked（脚本+资产烘焙）
├── binaries/         # 内核镜像、console、libnvram、arm64 initramfs 等资产
├── tools/            # QEMU fork 管理、镜像构建等辅助脚本（规划中）
├── tests/            # 单元测试与评测集回归
└── CHANGELOG.md      # 版本变更记录
```

## 文档索引

**`docs/` —— 开发与部署**

| 文档 | 内容 |
|---|---|
| [01-开发规划](docs/01-开发规划.md) | 五层架构（L1–L5）、里程碑 M0–M5、与 FirmAE/FirmPilot 的对比分析 |
| [02-技术栈规划](docs/02-技术栈规划.md) | 依赖选型、工程规范 |
| [03-M0执行手册](docs/03-M0执行手册.md) | M0 技术验证步骤 |
| [04-快速部署](docs/04-快速部署.md) | 快速部署与迁移手册，含故障速查表 |
| [05-崩溃归因](docs/05-崩溃归因.md) | 崩溃诊断方法论与 TES7002 案例技术细节 |
| [06-稳定性验证](docs/06-稳定性验证.md) | 长时运行稳定性验证方法与观察记录 |
| [07-架构映射修复](docs/07-架构映射修复.md) | aarch64 → arm64 架构标签映射 |
| [eval-log](docs/eval-log.md) | 评测集逐设备实测日志指纹 |

**`docx/` —— 使用与治理**

| 文档 | 内容 |
|---|---|
| [免安装使用指南](docx/免安装使用指南.md) | 不做 pip 安装直接从源码运行；四种启动方式、命令速查、故障排查 |
| [AI值守与稳定性治理](docx/AI值守与稳定性治理.md) | TES7002 实战治理全过程：三类故障的证据链、根因、修复，以及 AI 值守能力设计 |

## 里程碑（详见 docs/01-开发规划.md）

M0 技术验证 → M1 MVP 主干 → M2 规模化 → M3 交互与分析 → M4 智能环境恢复 → M5 产品化

## 已知限制

- **arm64 通道无 libnvram / console 劫持**：FirmAE 未发布 aarch64 插桩内核，依赖 nvram 的厂商服务在 arm64 下起不来（属通道设计取舍，非缺陷）；
- **厂商网络模型强绑定真实存储**：如 Tenda configd 依赖 `ubi0:ubi_Config` 挂载 `/var/config`，仿真环境无该分区时 eth0 不获址，由 `iris_net_fix` 兜底（补 IP、放行 iptables、telnetd:7002、goahead/boa Web 拉起），TES7002 实测由此拿到可达 Web；
- **QEMU CPU 型号**：厂商 aarch64 二进制常用 ARMv8.3 指针认证（PAC），arm64 通道必须 `-cpu max`，否则 SIGILL；
- x86 语料不在仿真范围（当前仅 mipsel/mipseb/armel/arm64）。
- **失败知识已读回，但只到"可读"为止**：`iris db cards` 按失败种类聚合 `failure_profile`，读出该类失败出现在多少次 run、哪些镜像、哪些架构、最后一次何时、以及这些 run 上实际触发过哪些 L3 规则；`--promote-only` 只留最近若干次失败 run 里仍在出现的种类，即"还需要写规则的清单"（`network-fallback-ok` 这类信息类被排除，它表示兜底**正常工作**）。**不会**据此自动施加修复：一条规则有效的唯一证据是活体运行上的 `post_action_verify`，被记住的成功不是。剩余限制：`repair_action` 此前有表无写入路径（现已由 `emulate_firmware` 落库），但 `RuleReport.touched_files` 不落库——`prepare_from_firmware` 只保留命中的 rule id，因此无法回答"这次修补动了几处"；卡片上的 `recovered` 目前恒为 0，因为历史上没有任何成功 run 带过失败行。
- **链路已主动测量，但仅在失败路径上**：`iris.emulate.linkprobe` 会分层实测 route / ARP / ICMP / service 并产出链路分层表，且只在 Web 超时的失败路径上运行——成功路径不做探测。ARP 层在没有邻居表条目时**无法区分「没有这个地址」与「ARP 问过但没人应」**，此时该层记 `unknown`，verdict 的 detail 会显式写出「哪一层没测到」。
- **已能直接读写 guest 文件系统，但值守还用不上**：`iris guest ls/get/put` 在特权容器内对 guest 镜像做 loop 挂载（与 `make_image.sh` 构建期同一操作），`guest put` 注入的代码会在下次启动被 guest 执行（2026-10-04 DIR-868L 实测：串口出现注入的 `IRIS-REPAIR-PROOF`，Web 仍 HTTP 200）。两条边界：看到的是**磁盘上的文件**，不是运行中 guest 的视图（内存缓冲、被挂载覆盖的路径都不算）；访问状态盘要求 **QEMU 已停止**，而值守的动作都发生在它运行时，所以值守侧 `n=0` 依旧读作 UNKNOWN。详见 `docx/AI值守与稳定性治理.md` §6.4 与 §9。
- **运行期写入不再丢弃，但首启观测没有被重启复现**：`run_qemu.sh` 改用持久的 `state.raw`（出厂镜像 `image.raw` 保持不动，可随时 `iris guest reset <iid>` 回到出厂状态），guest 与修补的写入能跨启动保留。**未修**：`WEB_SERVER_RESTART` 重跑时不会重复首启那次"把宿主桥地址补进 guest 自己子网"的观测（实测重启后 `curl` 为 `HTTP 000`，手动补上同一地址立刻 `HTTP 200`），根因是首启检测到的 guest 地址没有落盘。
- **语料规模**：当前 5 个 M1 设备，离任何可承诺的成功率都还很远。

## 协作规范（强制）

1. **每个任务推进必须建立本地 Git commit 节点**，以便随时回滚；遵循 Conventional Commits（`feat:` / `fix:` / `docs:` / `chore:` / `refactor:` / `test:`）；
2. 阶段性成果用 tag 标注里程碑（如 `m1-mvp`）；
3. 大型改动拆分为多个可独立回滚的 commit，禁止一次性巨型提交。
