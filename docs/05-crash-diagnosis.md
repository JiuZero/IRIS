# 登录功能键服务崩溃 - 问题排查报告

## 问题现象

用户触发仿真环境的登录按钮时，`iris emulate run iris-home/scratch/US_TES7002... --arch arm64` 的会话记录：

- Host 端口 `8090` → 返回 `302 /login.html`（HTTP 正常）
- 加载 `/js/login.c9f2fcf4.js`、`/js/chunk-common.e69bef0d.js` 等静态资源（部分成功，部分返回 `000`）
- POST `http://127.0.0.1:8090/setModules` 携带正确 payload 返回 `{"errorCode":20}`（业务错误，非服务崩溃）
- **Guest web (goahead) 随即死亡**：host 请求 `/login.html` 返回 `000`；内部探测 `192.168.1.1:80` 显示 `GUEST80_DEAD`
- QEMU 与 socat 进程存活，但 goahead 失去响应
- 串口日志仅显示 `diag: fatal signal 11` 重复循环（这是 benign 噪声，源于缺少 `/proc/fc`）

## 根本原因分析

经过深入追踪 serial log，发现以下序列（第 5959 号仿真）：

```
[2160] IRIS-NETFIX: done
[2169] Monitor: process gp8 is die.
[2170] sh: reboot: not found
[2171] sysrq: Resetting
[2172] [    0.000000] Booting Linux on physical CPU 0x0000000000 ...
```

关键观察点：

1. **厂商监控守护进程**：rootfs 中的 `inittab::once:-/bin/monitor` 从 `/opt/monitor` 启动（symlink via `/bin/monitor`），以 `::once` 形式由 vendor busybox init 执行一次。

2. **看门狗机制**：monitor 二进制字符串表明其实现为：
   ```sh
   ps | grep -w "%s" | egrep -v grep | awk '{ print $1 }'
   # monitored: ubusd, goahead, gp8, swg
   # action on death: date >> /mnt/log/monitor.log; echo b > /proc/sysrq-trigger
   ```
   gp8/swg 依赖 RealTek GPON MAC (`/proc/fc/*`)，在 QEMU 环境下从未启动，所以 monitor 永远检测到 "process is die"。

3. **SysRQ 整机重启**：vendor init 脚本没有实际的 `reboot` 命令支持，只能硬敲 `echo b > /proc/sysrq-trigger`，导致整个 guest 重启。实测 3 次重启周期（约每 20 分钟一次）。

4. **与登录触发的关联**：用户点击登录 → goahead 处理 request → monitor 轮询时发现 goahead 还在运行但 gp8/swg 不存在 → 判定异常 → trigger reboot → host web 请求返回 `000`。

### 证据链

| Line | Content | Interpretation |
|------|---------|----------------|
| 2169 | `Monitor: process gp8 is die.` | Watchdog detects missing hardware-bound process |
| 2170 | `sh: reboot: not found` | No actual reboot command; falls back to sysrq |
| 2171 | `sysrq: Resetting` | Force reset via /proc/sysrq-trigger = reboot |
| 2172 | `[0.000000] Booting Linux...` | Fresh kernel boot after reset |
| 4340-4344 | Same pattern again | Reboot cadence confirmed |
| 6513-6516 | Same pattern | Third reboot |
| Serial tail | Only `diag: signal 11` repeats | Benign hardware driver noise |

**结论**：登录按钮点击只是暴露了问题的时刻，真正的根因是厂商 monitor 看门狗无法容忍缺硬件的环境，周期性触发 sysrq 重启整个 guest。goahead 在 login POST 期间存活并返回 HTTP 200，但由于 monitor 的死锁检测窗口，不久后就被重置杀死。

## 解决方案

### 方案 A：静音 monitor 守护进程（推荐）

通过 L3 规则引擎直接移除 watchog，不再影响 guest 运行：

**新规则文件** `rules/vendor-watchdog-monitor.yaml`：

```yaml
id: vendor-watchdog-monitor
description: >
  Vendor supervision daemons reboot the whole guest when a hardware-bound
  process is missing. Tenda TES7002 (aarch64 GPON OLT) runs /bin/monitor ->
  /opt/monitor from the inittab line "::once:-/bin/monitor"; it polls
  `ps | grep -w <name>` for ubusd/goahead/gp8/swg and on any miss executes
  `reboot` and then `echo 1 > /proc/sys/kernel/sysrq` +
  `echo b > /proc/sysrq-trigger`. gp8/swg need the RealTek /proc/fc control
  files and a GPON MAC, so they can never come up under QEMU and the guest
  hard-resets minutes after every boot — which reads as "the web service
  crashed" while QEMU and the port forward stay alive. Commenting inittab
  alone is not enough (rcS chains can re-exec the watchdog), so the binaries
  are also renamed out of the way; guest_shell here runs in chroot at image
  build time (fix_image.sh), never against the host /proc.
  Evidence: 5959/qemu.serial.log — 3x "Monitor: process gp8 is die." each
  followed by "sh: reboot: not found" + "sysrq: Resetting" + a fresh kernel
  boot banner.
stage: service
detect:
  - file_regex: '::(once|respawn):[^\n]*/(monitor|watchdog|keep_alive|keepalive|wdt)(\s|$)'
    within: 'etc/inittab'
actions:
  - comment_lines:
      within: 'etc/inittab'
      regex: '::(once|respawn):[^\n]*/(monitor|watchdog|keep_alive|keepalive|wdt)(\s|$)'
      prefix: '#IRIS-watchdog: '
  - guest_shell:
      - "# supervision daemons renamed out of PATH and out of their own dirs"
      - "for b in /bin/monitor /sbin/monitor /usr/bin/monitor /usr/sbin/monitor \\"
      - "        /opt/monitor /opt/bin/monitor /bin/watchdog /sbin/watchdog \\"
      - "        /usr/bin/watchdog /usr/sbin/watchdog /etc/scripts/watchdog; do"
      - "  [ -e \"$b\" ] && mv -f \"$b\" \"$b.iris-disabled\" 2>/dev/null"
      - "done"
```

**效果预期**：

- `inittab` 被注释掉，不会启动 monitor
- `/opt/monitor` 等二进制文件被重命名为 `.iris-disabled`，防止 rcS 或其他脚调用
- guest 进入休眠后的 uptime 周期大幅延长（不再有周期性 reboot）
- goahead 可长期稳定运行，允许完整的功能测试和登录流程验证

**实施步骤**：

1. 将规则 YAML 写入 `rules/vendor-watchdog-monitor.yaml`
2. 对提取的 rootfs 运行 `python -m iris.cli rules apply <rootfs-dir> --apply`
3. 重新构建镜像 `docker run --rm -it -v scripts:/work/scripts -v iris-home/scratch:/work/scratch iris-emulate-baked bash /work/scripts/make_image.sh <iid> arm64`
4. 启动仿真 `iris emulate run <rootfs-dir> --arch arm64 --port 8090 --timeout 600`

### 方案 B：修补 monitor 轮询条件（保守）

修改 monitor 的二进制行为，添加额外的启动检查条件（如存在某个文件），不直接静音。缺点是需要 patch ELF 或 hook，复杂度高于方案 A。

## 代码变更

### 1. 引擎层防护（防误写 ELF）

`src/iris/rules/engine.py` 新增 `_is_binary()` 函数，所有文本编辑操作前检测文件特征（512 字节内含 `\x00` 即视为二进制）：

```python
def _is_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(512)
    except OSError:
        return True
```

编辑器和注释行操作的循环中插入：
```python
if _is_binary(f):
    continue
```

确保规则引擎永远不破坏 vendor ELF。

### 2. 网络修复脚本优化

`scripts/emulate/iris_net_fix.sh`：

- 新增 `web_running()` 函数，优先检查进程（`pidof goahead`）而非 netstat 端口监听（避免 misreport）
- 新增 `port80_listening()` 作为 fallback
- 逻辑顺序：先查进程，再查 netstat，最后才拉起 goahead（避免双实例 errno 98）

```sh
web_running() {
    for p in goahead boa lighttpd httpd uhttpd thttpd apache2 nginx; do
        pidof "$p" >/dev/null 2>&1 && return 0
        ps 2>/dev/null | grep -w "$p" | grep -v grep >/dev/null 2>&1 && return 0
    done
    return 1
}
```

### 3. 自动管道功能（feat.emulate）

新模块 `src/iris/emulate/auto.py`：

- `prepare_from_firmware()`：一键提取固件→应用规则→推断架构
- `prepare_from_rootfs()`：封装现有 rootfs，只推断 arch+report 规则匹配
- `pick_host_port()`：自动分配空闲宿主端口（8080-8199）

CLI 增强 `emulate_run()`：

- `target` 改为既可以接受 rootfs 目录，也可以接受固件 `.bin`
- `--arch` 变为可选参数，默认 `auto`（从 ELF census 推断）
- `--port` 支持 `0` 表示自动选择空闲端口
- `--apply-rules/--no-apply-rules` 开关 L3 规则应用（默认开启）

用法示例：
```bash
# 固件直发，自动架构+端口
iris emulate run firmware.bin

# 指定架构，启用规则，自动选端口
iris emulate run firmware.bin --arch arm64 --apply-rules --port 0

# 已有 rootfs，自动推断架构
iris emulate run ./tes7002-rootfs --arch auto
```

## 验证计划

1. 应用规则后重构根文件系统，验证 `/opt/monitor` 被重命名
2. 运行新图像至少 10 分钟，检查 serial log 是否还有 `process .* is die` 记录
3. 进行登录流程测试，确认 goahead 长期存活（>30 分钟无重启）
4. 自动化单元测试验证规则行为（已在 `tests/test_rules.py` 中添加）

## 参考资料

- Serial log excerpt: `iris-home/scratch/5959/qemu.serial.log` 第 2160-2172 行
- Rule schema examples: `rules/dev-extended-nodes.yaml`, `rules/shadow-jffs2-opt.yaml`
- New rule file: `rules/vendor-watchdog-monitor.yaml`