import { Link } from 'react-router-dom'
import { ArrowRight, Boxes, Gauge, HardDrive, Layers, Terminal, Timer, TrendingUp, Wrench } from 'lucide-react'

import { LaunchPanel } from '../components/LaunchPanel'
import { Badge, Button, EmptyState, ErrorState, Meter, Panel, Skeleton, StatTile, StatusDot } from '../components/ui'
import { classNames, DASH, failureLabel, formatDuration, megabytes, percent } from '../lib/format'
import { useCapabilities, useEvalSet, useRootCauses, useStats, useSystem } from '../hooks/queries'


/**
 * The landing screen, in the order the questions actually get asked: *can this
 * machine do it*, *what has it done*, *start something*, *why did it fail*.
 *
 * The environment strip comes first because every other number on this page is a
 * claim about work this host performed -- a 34% reach rate means nothing without
 * knowing the CPU and disk it ran on, and a launch that will fail for want of disk
 * is worth knowing about before the form rather than after it.
 *
 * Every card is a live read of the same tables the rest of the workbench reads, so
 * nothing here can drift from what the detail pages show. Nothing on this page is
 * a stored sample: there is no fixture and no placeholder behind any of it.
 */
export function Dashboard() {
  const stats = useStats()
  const capabilities = useCapabilities()
  const evalSet = useEvalSet()
  const causes = useRootCauses(6)
  const system = useSystem()

  if (stats.isError) {
    const error = stats.error as { status?: number; message?: string; isAuth?: boolean }
    return (
      <div className="p-4">
        <Panel title="无法读取统计">
          <ErrorState
            title={error?.isAuth ? '需要 API 令牌' : 'IRIS 服务不可用'}
            detail={
              error?.isAuth
                ? '当前服务配置了 IRIS_API_TOKEN。请在设置页填入同一个令牌，或用 iris web 启动而不配置令牌（仅回环地址）'
                : error?.message
            }
            hint={
              error?.isAuth ? (
                <Link to="/settings" className="text-2xs text-iris-400 hover:underline">
                  去设置页填入令牌
                </Link>
              ) : null
            }
            onRetry={() => void stats.refetch()}
          />
        </Panel>
      </div>
    )
  }

  const data = stats.data
  /* Every figure below is the server's own count. `?? 0` would be the wrong
     default twice over: the server sends `null` for "this table could not be read",
     and rendering that as `0` claims an empty database rather than an unreadable
     one. `stats.available` is the gate that says which it is, so a card only
     appears when the number behind it is a measurement. */
  const cards = data?.available
    ? [
        {
          label: '仿真记录',
          value: String(data.total),
          unit: '条',
          tone: 'neutral' as const,
          hint: '全库累计，含同一固件的多次运行',
          icon: <Layers className="h-4 w-4 text-ink-700" aria-hidden="true" />,
        },
        {
          label: 'Web 可达',
          value: String(data.web_ok),
          unit: '条',
          tone: 'success' as const,
          hint: `占全部记录 ${percent(data.web_reach_rate)}`,
          icon: <TrendingUp className="h-4 w-4 text-ink-700" aria-hidden="true" />,
        },
        {
          label: '二层/三层环境失败',
          value: String(data.environment_failures),
          unit: '次',
          tone: 'warning' as const,
          hint: 'ARP/路由/ICMP 不通，与四层未起服务分开计',
          icon: <Wrench className="h-4 w-4 text-ink-700" aria-hidden="true" />,
        },
        {
          label: '运行中实例',
          value: String(data.running),
          unit: '个',
          tone: 'iris' as const,
          hint: '由本服务托管，可在实例页接入终端',
          icon: <Boxes className="h-4 w-4 text-ink-700" aria-hidden="true" />,
        },
      ]
    : []

  return (
    <div className="flex flex-col gap-4 p-4">
      <EnvironmentStrip query={system} />

      <section aria-label="总览指标" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {stats.isLoading
          ? Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-24" />)
          : cards.map((card) => (
              <StatTile key={card.label} {...card} />
            ))}
      </section>

      {data && !data.available && (
        <Panel title="统计不可用">
          <p className="text-2xs text-ink-500">{data.detail ?? '元数据库无法读取'}</p>
        </Panel>
      )}

      <LaunchPanel />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <EvalSetPanel query={evalSet} />
        <ArchPanel stats={data} />
        <CapabilityPanel query={capabilities} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <FailurePanel causes={causes} />
        <QuickStart />
      </div>
    </div>
  )
}

/**
 * What this host can do right now, read live.
 *
 * Every figure is sampled at request time by `iris.api.host_metrics` -- there is no
 * stored reading and nothing to fall back to, so a host that refuses to answer
 * shows dashes rather than plausible numbers. The CPU share is `null` on the very
 * first sample by construction: computing a share of host CPU needs two counter
 * reads, and inventing a `0` for the gap would report an idle machine on a machine
 * that is busy.
 */
function EnvironmentStrip({ query }: { query: ReturnType<typeof useSystem> }) {
  const host = query.data?.host
  const service = query.data?.service

  if (query.isError) {
    return (
      <Panel
        title="环境状态"
        subtitle="CPU、内存与暂存盘剩余空间，实时采样"
        actions={<Badge tone="warning">采样不可用</Badge>}
      >
        <ErrorState
          title="无法读取宿主资源"
          detail={(query.error as Error).message}
          onRetry={() => void query.refetch()}
        />
      </Panel>
    )
  }

  // A ratio needs both halves measured; `mem_total_mb` alone would divide by a
  // number the same read could not supply.
  const memRatio =
    host?.mem_total_mb && host.mem_used_mb != null ? host.mem_used_mb / host.mem_total_mb : null

  return (
    <Panel
      title="环境状态"
      subtitle="CPU、内存与暂存盘剩余空间；实时采样，非历史统计"
      actions={
        <span className="flex items-center gap-2">
          {query.isFetching && (
            <span className="flex items-center gap-1 text-2xs text-ink-700">
              <StatusDot tone="iris" pulse />
              重新采样
            </span>
          )}
          <Badge tone="neutral" title="服务自身版本，随每次采样一起下发">
            IRIS {service?.version ?? DASH}
          </Badge>
        </span>
      }
    >
      {query.isLoading && !query.data && <Skeleton className="h-28 w-full" />}
      {query.data && (
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:gap-6">
          {/* Two bars, not three. Uptime is a figure with no natural maximum, so a
              bar for it would need an arbitrary denominator and would sit pinned at
              full on any host that has been up for a day; it reads as a number in the
              table beside these, which is what uptime actually is. */}
          <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
            <Meter
              label="CPU"
              value={host?.cpu_pct == null ? null : host.cpu_pct / 100}
              caption={host?.cpu_cores ? `· ${host.cpu_cores} 核` : undefined}
              tone={host?.cpu_pct == null ? 'neutral' : host.cpu_pct >= 90 ? 'danger' : host.cpu_pct >= 75 ? 'warning' : 'iris'}
            />
            <Meter
              label="内存"
              value={memRatio}
              tone={memRatio == null ? 'neutral' : memRatio >= 0.9 ? 'danger' : memRatio >= 0.8 ? 'warning' : 'iris'}
            />
          </div>

          <dl className="grid flex-none grid-cols-2 gap-x-5 gap-y-1.5 border-t border-surface-border pt-3 text-2xs lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
            <dt className="text-ink-500">内存占用</dt>
            <dd className="tnum text-right text-ink-100">
              {megabytes(host?.mem_used_mb)} / {megabytes(host?.mem_total_mb)}
            </dd>
            <dt className="flex items-center gap-1 text-ink-500">
              <HardDrive className="h-3 w-3" aria-hidden="true" />
              暂存盘剩余
            </dt>
            <dd className="tnum text-right text-ink-100" title="所有容器的 rootfs 归档都落在这个卷上">
              {host?.scratch_free_gb == null ? DASH : `${host.scratch_free_gb.toFixed(1)} GB`}
            </dd>
            <dt className="flex items-center gap-1 text-ink-500">
              <Timer className="h-3 w-3" aria-hidden="true" />
              宿主已运行
            </dt>
            <dd className="tnum text-right text-ink-100">
              {host?.uptime_sec == null ? DASH : formatDuration(host.uptime_sec)}
            </dd>
            <dt className="text-ink-500">服务已运行</dt>
            <dd className="tnum text-right text-ink-100" title="本进程自己的单调时钟，与宿主运行时长不是一回事">
              {service?.uptime_sec == null ? DASH : formatDuration(service.uptime_sec)}
            </dd>
          </dl>
        </div>
      )}
      <p className="mt-3 border-t border-surface-border pt-2 text-[10px] leading-relaxed text-ink-700">
        这里的每个数字都在请求时现采，没有缓存的历史读数。CPU 占比需要两次计数器读数之差，所以首次采样显示
        {host?.cpu_pct == null ? '未测量' : '最新值'}而非 0%；同一个 {service?.sample_ttl_sec ?? DASH} 秒窗口内的重复请求会拿到同一个数字
      </p>
    </Panel>
  )
}



function EvalSetPanel({ query }: { query: ReturnType<typeof useEvalSet> }) {
  return (
    <Panel
      title="M1 评测集（全库累计）"
      subtitle={query.data?.scope}
      actions={
        <Badge tone="iris">
          {query.data ? `${query.data.web_ok}/${query.data.denominator}` : DASH}
        </Badge>
      }
    >
      {query.isLoading && <Skeleton className="h-32 w-full" />}
      {query.data && (
        <div className="flex flex-col gap-3">
          <div className="flex items-baseline gap-2">
            <span className="tnum text-3xl font-semibold text-iris-400">
              {percent(query.data.web_reach_rate)}
            </span>
            <span className="text-2xs text-ink-500">Web 可达率</span>
          </div>
          <ul className="flex flex-col gap-1">
            {(Object.entries(query.data.by_arch) ?? []).map(([arch, counts]) => (
              <li key={arch} className="flex items-center gap-2">
                <span className="w-14 shrink-0 font-mono text-2xs text-ink-300">{arch || '?'}</span>
                <ArchBar seen={counts.seen} ok={counts.web_ok} />
                <span className="tnum shrink-0 text-2xs text-ink-500">
                  {counts.web_ok}/{counts.seen}
                </span>
              </li>
            ))}
          </ul>
          <p className="border-t border-surface-border pt-2 text-[10px] leading-relaxed text-ink-700">
            {query.data.denominator_note}
          </p>
        </div>
      )}
    </Panel>
  )
}

function ArchBar({ seen, ok }: { seen: number; ok: number }) {
  const ratio = seen > 0 ? ok / seen : 0
  return (
    <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-hover" aria-hidden="true">
      <span
        className="block h-full rounded-full bg-gradient-to-r from-iris-600 to-iris-400"
        style={{ width: `${Math.round(ratio * 100)}%` }}
      />
    </span>
  )
}

function ArchPanel({ stats }: { stats: ReturnType<typeof useStats>['data'] }) {
  const byArch = stats?.by_arch ?? {}
  const rows = Object.entries(byArch)
  return (
    <Panel title="按架构" subtitle="各架构的记录数与 Web 可达数">
      {rows.length === 0 ? (
        <EmptyState title="暂无记录" detail="启动一次仿真后这里会出现分布" />
      ) : (
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-surface-border text-2xs text-ink-500">
              <th className="py-1 font-medium">架构</th>
              <th className="py-1 text-right font-medium">记录</th>
              <th className="py-1 text-right font-medium">可达</th>
              <th className="py-1 text-right font-medium">可达率</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([arch, counts]) => (
              <tr key={arch} className="border-b border-surface-border/60 last:border-0">
                <td className="py-1 font-mono text-ink-100">{arch || '?'}</td>
                <td className="tnum py-1 text-right text-ink-300">{counts.seen}</td>
                <td className="tnum py-1 text-right text-ink-300">{counts.web_ok}</td>
                <td className="tnum py-1 text-right text-iris-400">
                  {counts.seen ? percent(counts.web_ok / counts.seen) : DASH}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-2 text-[10px] text-ink-700">合计 Web 可达率：{percent(stats?.web_reach_rate ?? null)}</p>
    </Panel>
  )
}

function CapabilityPanel({ query }: { query: ReturnType<typeof useCapabilities> }) {
  return (
    <Panel
      title="能力矩阵"
      subtitle="状态由后端探测构建内容得出"
      actions={<Badge tone="neutral">v{query.data?.version ?? DASH}</Badge>}
    >
      {query.isLoading && <Skeleton className="h-32 w-full" />}
      <ul className="flex flex-col gap-1.5">
        {(query.data?.items ?? []).map((item) => (
          <li key={item.id} className="flex items-start gap-2">
            <StatusDot
              tone={item.state === 'available' ? 'success' : item.state === 'planned' ? 'warning' : 'danger'}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-ink-100">{item.name}</span>
                <span
                  className={classNames(
                    'text-[10px]',
                    item.state === 'available' ? 'text-ink-700' : 'text-warning',
                  )}
                >
                  {item.state === 'available' ? '就绪' : item.state === 'planned' ? '待改造' : '不含模型'}
                </span>
              </div>
              <p className="truncate text-[10px] text-ink-700" title={item.detail}>
                {item.detail}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function FailurePanel({ causes }: { causes: ReturnType<typeof useRootCauses> }) {
  const cards = causes.data?.cards ?? []
  return (
    <Panel
      title="失败聚类"
      subtitle="按最近出现排序；候选标记表示近期仍在发生"
      actions={
        <Link to="/instances" className="text-2xs text-iris-400 hover:underline">
          查看全部记录
        </Link>
      }
    >
      {causes.isLoading && <Skeleton className="h-24 w-full" />}
      {!causes.isLoading && cards.length === 0 && (
        <EmptyState title="暂无失败聚类" detail="所有已记录的仿真都没有留下失败信号" />
      )}
      <ul className="flex flex-col gap-1.5">
        {cards.slice(0, 6).map((card) => (
          <li
            key={`${card.stage}-${card.kind}`}
            className="flex items-center gap-2 rounded-card border border-surface-border px-2 py-1.5"
          >
            <StatusDot tone={card.candidate ? 'warning' : 'neutral'} />
            <span className="flex-1 truncate text-xs text-ink-100">{failureLabel(card.kind)}</span>
            <span className="shrink-0 font-mono text-[10px] text-ink-700">{card.stage || '—'}</span>
            <span className="tnum shrink-0 text-2xs text-ink-500">{card.runs} 次</span>
            {card.recovered > 0 && (
              <Badge tone="success">已恢复 {card.recovered}</Badge>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  )
}

/** The four things a first-time viewer should try, in the order that works without
 *  any setup. Each links somewhere that exists, rather than describing a command.
 *
 *  The first two are the two halves of the launch that used to be one page: the
 *  form is above this panel, and the records it produces are on `/instances`. A
 *  step that points at "the instances page" for starting something was a lie about
 *  where the work happens; each of the four now names one destination. */
function QuickStart() {
  const steps = [
    { title: '启动一次仿真', detail: '用本页「新建实例」的三种固件来源之一；启动过程结束即返回判定', to: '/', icon: <Boxes className="h-4 w-4" /> },
    { title: '接入交互式终端', detail: 'QEMU 串口为可写 chardev，可回车执行命令', to: '/instances', icon: <Terminal className="h-4 w-4" /> },
    { title: '看四层链路证据', detail: '路由/ARP/ICMP/服务逐层给出结论与依据', to: '/instances', icon: <Gauge className="h-4 w-4" /> },
    { title: '核对这个构建能做什么', detail: '每条能力都带判定依据，不含模型调用的部分如实标注', to: '/settings', icon: <Wrench className="h-4 w-4" /> },
  ]
  return (
    <Panel title="快速开始" subtitle="无需额外配置；数据来自本机已记录的仿真">
      <ol className="flex flex-col gap-1.5">
        {steps.map((step, index) => (
          <li key={step.title}>
            <Link
              to={step.to}
              className="flex items-center gap-3 rounded-card border border-surface-border px-3 py-2 transition-colors hover:border-iris-400/50"
            >
              <span className="tnum w-4 shrink-0 text-2xs text-ink-700">{index + 1}</span>
              <span className="text-iris-400">{step.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="block text-xs text-ink-100">{step.title}</span>
                <span className="block truncate text-[10px] text-ink-700">{step.detail}</span>
              </span>
              <ArrowRight className="h-3.5 w-3.5 shrink-0 text-ink-700" aria-hidden="true" />
            </Link>
          </li>
        ))}
      </ol>
      <div className="mt-3 border-t border-surface-border pt-2">
        <Button size="sm" variant="ghost">
          <Terminal className="h-3.5 w-3.5" aria-hidden="true" />
          快捷键：Ctrl/Cmd+K 命令面板 · Ctrl/Cmd+` 终端 · Ctrl/Cmd+B 侧栏 · Ctrl/Cmd+J 失败抽屉
        </Button>
      </div>
    </Panel>
  )
}

