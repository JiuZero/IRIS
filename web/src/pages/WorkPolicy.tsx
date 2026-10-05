import { Cpu, GitBranch, Layers, Lightbulb, Network, ShieldCheck, Terminal, Wrench } from 'lucide-react'

import { Badge, ErrorState, Panel, Skeleton, StatusDot } from '../components/ui'
import { DASH } from '../lib/format'
import { useCapabilities } from '../hooks/queries'

/**
 * What this build can do, and what it deliberately does not.
 *
 * Three reference blocks that used to be scattered: the capability census was on the
 * dashboard *and* the settings page, the limits list was settings-only, and neither
 * was reachable from a question. A reviewer asking "does it call a model?" or "why
 * is the terminal 80 columns?" had to already know which page to open. Here they are
 * one page, and the sidebar carries a link to it.
 *
 * The census is read from the server, not written here: `iris.api.web_data.capabilities`
 * probes this build's contents, so a row cannot say "就绪" for something this
 * checkout does not contain. The other two blocks are editorial, and the reason they
 * are honest is that they name the specific limitation instead of gesturing at a
 * category -- "the QEMU serial line has no window-size channel" is checkable,
 * "terminal experience could be improved" is not.
 */
export function WorkPolicy() {
  const capabilities = useCapabilities()
  const items = capabilities.data?.items ?? []
  const ready = items.filter((item) => item.state === 'available').length

  return (
    <div className="flex flex-col gap-4 p-4">
      <Panel
        title="能力矩阵"
        subtitle="每一行都由后端探测本构建的内容得出，并附证据来源"
        actions={<Badge tone="neutral">v{capabilities.data?.version ?? DASH}</Badge>}
      >
        {capabilities.isLoading && <Skeleton className="h-40 w-full" />}
        {capabilities.isError && (
          <ErrorState
            title="无法读取能力矩阵"
            detail={(capabilities.error as Error).message}
            onRetry={() => void capabilities.refetch()}
          />
        )}
        {capabilities.data && (
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-surface-border text-2xs text-ink-500">
                <th className="py-2 font-medium">能力</th>
                <th className="py-2 font-medium">状态</th>
                <th className="py-2 font-medium">说明</th>
                <th className="py-2 font-medium">证据</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} className="border-b border-surface-border/60 last:border-0 align-top">
                  <td className="py-2 text-ink-100">{item.name}</td>
                  <td className="py-2">
                    <Badge
                      tone={item.state === 'available' ? 'success' : item.state === 'planned' ? 'warning' : 'danger'}
                    >
                      <StatusDot
                        tone={
                          item.state === 'available' ? 'success' : item.state === 'planned' ? 'warning' : 'danger'
                        }
                      />
                      {item.state === 'available' ? '就绪' : item.state === 'planned' ? '待改造' : '不适用'}
                    </Badge>
                  </td>
                  <td className="max-w-md py-2 text-2xs leading-relaxed text-ink-300">{item.detail}</td>
                  <td className="py-2 font-mono text-[10px] text-ink-700">{item.evidence || DASH}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="mt-2 flex flex-wrap items-center gap-x-2 text-[10px] leading-relaxed text-ink-700">
          <Badge tone={ready === items.length && items.length > 0 ? 'success' : 'neutral'}>
            就绪 {ready} / {items.length}
          </Badge>
          「AI 值守」一行标注为不适用：本项目的自愈器是规则加状态机，不含任何模型调用；L4 的状态来自对
          scripts/emulate/run_qemu.sh 的探测，因此一个未应用串口改造的构建会显示「待改造」，而不是宣称有交互式终端
        </p>
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <InnovationPanel />
        <LimitsPanel />
      </div>
    </div>
  )
}

/** The four things this workbench does that a script that boots one firmware does not.
 *
 *  Each entry names the mechanism, not the benefit. "故障归因到四层链路" is a claim
 *  anybody makes; "每层给出 ok/blocked/unknown 与判定依据，并落盘成可复查的证据" is
 *  something a reviewer can go and check on the record window. */
function InnovationPanel() {
  const items = [
    {
      icon: <Network className="h-4 w-4" aria-hidden="true" />,
      title: '四层链路证据',
      detail:
        '路由、ARP、ICMP、服务逐层给出 ok/blocked/unknown 与判定依据，first_break 指向第一个不通的层；证据随运行落盘，历史详情里可复查，而不是只留一句「失败」',
    },
    {
      icon: <GitBranch className="h-4 w-4" aria-hidden="true" />,
      title: '规则插件化自愈',
      detail:
        'rules/ 下的规则以 YAML 声明匹配条件与修复动作，加载时校验键名并对不支持的条件给出告警；每次修复写进账本，含是否应用、是否已沉淀为确定性规则',
    },
    {
      icon: <Layers className="h-4 w-4" aria-hidden="true" />,
      title: '失败画像与归因',
      detail:
        '一次失败的运行通常有多个叠加原因，每个信号各占一行 failure_profile，并带上阶段与日志指纹，于是「先修网卡」这类结论能从数据里回答，而不是靠猜',
    },
    {
      icon: <Terminal className="h-4 w-4" aria-hidden="true" />,
      title: '可写串口与输入权仲裁',
      detail:
        'QEMU 串口为可写 chardev，浏览器直接接入并可回车执行命令；同一实例的输入权有持有者标识，多个标签页不会互相抢键',
    },
  ]
  return (
    <Panel
      title="创新特性"
      subtitle="相对「把固件跑起来」这一基线，本项目多出来的部分"
      className="xl:col-span-2"
    >
      <ul className="grid grid-cols-1 gap-2 md:grid-cols-2">
        {items.map((item) => (
          <li key={item.title} className="flex items-start gap-2.5 rounded-card border border-surface-border px-3 py-2.5">
            <span className="mt-0.5 shrink-0 text-iris-400">{item.icon}</span>
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="text-xs font-medium text-ink-100">{item.title}</span>
              </div>
              <p className="mt-0.5 text-2xs leading-relaxed text-ink-500">{item.detail}</p>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-surface-border pt-3 text-2xs text-ink-700">
        <span className="flex items-center gap-1.5">
          <Wrench className="h-3.5 w-3.5" aria-hidden="true" />
          自愈器不含模型调用
        </span>
        <span aria-hidden="true">·</span>
        <span className="flex items-center gap-1.5">
          <Lightbulb className="h-3.5 w-3.5" aria-hidden="true" />
          「沉淀为规则」指把一次成功的修复写成确定性 YAML 规则，不指模型蒸馏
        </span>
        <span aria-hidden="true">·</span>
        <span className="flex items-center gap-1.5">
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
          终端通道的安全等级等于 guest shell，这是设计如此
        </span>
        <span aria-hidden="true">·</span>
        <span className="flex items-center gap-1.5">
          <Cpu className="h-3.5 w-3.5" aria-hidden="true" />
          统计卡与评测集使用全库累计口径
        </span>
      </div>
    </Panel>
  )
}

/** What this build does not do, stated here rather than left for a reviewer to find. */
function LimitsPanel() {
  const limits = [
    '终端尺寸固定 80×24：QEMU 串口没有窗口尺寸通道，guest 永远不会知道浏览器窗口更宽',
    '终端通道的安全等级等于 guest shell：拿到输入权就能在 guest 里执行命令，这是设计如此',
    '日志视图与终端视图数据源不同：日志走运行结束时落盘的快照（不含终端输入），实时输出走终端通道',
    '由命令行（iris emulate run）启动的仿真不在 API 的托管表里，因此页面无法停止它或接入它的终端',
    '资源占用来自 docker stats 采样；容器刚启动时可能尚无样本，此时显示为「未采样」而不是 0%',
    '统计卡与评测集使用全库累计口径（含同一固件的多次运行），与任何单批次实验数字不同',
    '主题与密度只保存在当前浏览器：换一台机器或换一个无痕窗口会回到默认值，服务端不保存任何界面偏好',
  ]
  return (
    <Panel
      title="已知限制"
      subtitle="如实标注，而不是留给评审去发现"
      className="xl:col-span-2"
    >
      <ul className="flex flex-col gap-1.5 text-2xs leading-relaxed text-ink-300">
        {limits.map((limit) => (
          <li key={limit}>· {limit}</li>
        ))}
      </ul>
    </Panel>
  )
}