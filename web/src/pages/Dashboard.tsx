import { Link } from 'react-router-dom'
import { ArrowRight, Boxes, Gauge, Layers, ListChecks, Terminal, TrendingUp, Wrench } from 'lucide-react'

import {
  ArchBar,
  Badge,
  Button,
  EmptyState,
  ErrorState,
  Panel,
  Skeleton,
  SpanBar,
  StatTile,
  StatusDot,
} from '../components/ui'
import { DASH, failureLabel, percent, seconds } from '../lib/format'
import {
  useCorpus,
  useEvalSet,
  useFailureMatrix,
  useLatency,
  useRootCauses,
  useStats,
} from '../hooks/queries'
import { useUiStore } from '../store/ui'


/**
 * The landing screen, in the order the questions actually get asked: *what has this
 * host done*, *how well*, *why did it fail*, *what next*.
 *
 * Only the cumulative record lives here. The host's live reading is in the rail and
 * the launch window: those two are a pairing -- "can this machine take another run"
 * and "here is the run" -- and putting the meter above the statistics made the
 * dashboard answer a question about the *present* with the most important number on
 * it being about the past. The capability census moved to `/work-policy` for the
 * same reason: a row that says "待改造" is a policy statement, not a metric, and
 * three copies of it on three screens is three chances to drift.
 *
 * Every card is a live read of the same tables the rest of the workbench reads, so
 * nothing here can drift from what the detail pages show. Nothing on this page is
 * a stored sample: there is no fixture and no placeholder behind any of it.
 */
export function Dashboard() {
  const stats = useStats()
  const evalSet = useEvalSet()
  const causes = useRootCauses(6)
  const corpus = useCorpus()
  const latency = useLatency()
  const matrix = useFailureMatrix()
  const openLaunch = useUiStore((state) => state.openLaunch)
  const openSettings = useUiStore((state) => state.openSettings)

  if (stats.isError) {
    const error = stats.error as { status?: number; message?: string; isAuth?: boolean }
    return (
      <div className="p-4">
        <Panel title="无法读取统计">
          <ErrorState
            title={error?.isAuth ? '需要 API 令牌' : 'IRIS 服务不可用'}
            detail={
              error?.isAuth
                ? '当前服务配置了 IRIS_API_TOKEN，请在设置里填入同一个令牌，或用 iris web 启动而不配置令牌（仅回环地址）'
                : error?.message
            }
            hint={
              error?.isAuth ? (
                <button type="button" onClick={openSettings} className="text-2xs text-iris-400 hover:underline">
                  打开设置填入令牌
                </button>
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

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <EvalSetPanel query={evalSet} />
        <ArchPanel stats={data} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <FailurePanel causes={causes} />
        <QuickStart onLaunch={openLaunch} />
      </div>

      <CorpusPanel query={corpus} />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <LatencyPanel query={latency} />
        <FailureMatrixPanel query={matrix} />
      </div>
    </div>
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

/** What a first-time viewer should try, in the order that works without any setup.
 *
 *  Each step names one destination and does at most one thing itself. The first step
 *  opens the launch window right here: a step whose only effect is to change the URL
 *  leaves the viewer hunting for a button that is not on the page they were sent to,
 *  which is what "启动一次仿真" used to do. The last two are the two reference pages
 *  the rail carries -- the rule inventory and the capability census -- because a
 *  first-time reader's next real question after "what did it do" is "what is in the
 *  box". */
function QuickStart({ onLaunch }: { onLaunch: () => void }) {
  const steps = [
    {
      title: '启动一次仿真',
      detail: '三种固件来源可选；启动过程结束即返回判定',
      icon: <Boxes className="h-4 w-4" />,
      action: onLaunch,
    },
    {
      title: '接入交互式终端',
      detail: 'QEMU 串口为可写 chardev，可回车执行命令',
      to: '/instances',
      icon: <Terminal className="h-4 w-4" />,
    },
    {
      title: '看四层链路证据',
      detail: '路由/ARP/ICMP/服务逐层给出结论与依据',
      to: '/instances',
      icon: <Gauge className="h-4 w-4" />,
    },
    {
      title: '核对这个构建能做什么',
      detail: '能力矩阵每条带判定依据，不含模型调用的部分如实标注',
      to: '/work-policy',
      icon: <ListChecks className="h-4 w-4" />,
    },
    {
      title: '看规则插件库',
      detail: '内置规则插件的匹配条件与修复动作，账本记账逐条可核',
      to: '/plugins',
      icon: <Wrench className="h-4 w-4" />,
    },
  ]
  return (
    <Panel title="快速开始" subtitle="无需额外配置；数据来自本机已记录的仿真">
      <ol className="flex flex-col gap-1.5">
        {steps.map((step, index) => (
          <li key={step.title}>
            <Link
              to={step.to ?? '/'}
              onClick={
                step.action
                  ? (event) => {
                      // An action, not a destination: navigating as well would push a
                      // history entry that went nowhere and scroll the page under it.
                      event.preventDefault()
                      step.action?.()
                    }
                  : undefined
              }
              className="flex items-center gap-3 rounded-card border border-surface-border px-3 py-2 transition-colors hover:border-iris-400/50"
            >
              <span className="tnum w-4 shrink-0 text-2xs text-ink-700">{index + 1}</span>
              <span className="text-iris-400">{step.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="block text-xs text-ink-100">{step.title}</span>
                <span className="block truncate text-[10px] text-ink-700">{step.detail}</span>
              </span>
              {step.action ? (
                <Button size="sm" variant="outline" tabIndex={-1} aria-hidden="true">
                  新建实例
                </Button>
              ) : (
                <ArrowRight className="h-3.5 w-3.5 shrink-0 text-ink-700" aria-hidden="true" />
              )}
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
/**
 * One row per firmware: how often it ran, whether its web plane ever answered, and
 * what the most recent run's primary cause was.
 *
 * The other two panels on this page answer *how well* and *why*; this one answers
 * *which firmware*, which is the question a corpus actually starts from. The
 * `totals` beside it is counted by the server from the same function the stat cards
 * read, so the row column and the badge above it cannot end up describing two
 * different libraries.
 *
 * Full width on purpose: the label is a file name, and a truncated firmware name is
 * worse than no table -- it is the column a reader would most want to trust.
 */
function CorpusPanel({ query }: { query: ReturnType<typeof useCorpus> }) {
  const data = query.data
  const totals = data?.totals
  return (
    <Panel
      title="固件语料"
      subtitle="按固件聚合的已记录运行：跑过多少次、Web 面上过几次、最近一次的主因"
      actions={
        <Badge tone="iris" title="Web 可达 / 全部运行">
          {totals ? `${totals.web_ok}/${totals.runs}` : DASH}
        </Badge>
      }
    >
      {query.isLoading && <Skeleton className="h-32 w-full" />}
      {data && data.firmwares.length === 0 && (
        <EmptyState
          title="暂无可归集的固件行"
          detail="运行记录要先能落到一个已登记的固件上；未归属的运行单独列在表尾，不会摊到任何固件"
        />
      )}
      {data && data.firmwares.length > 0 && (
        <div className="flex flex-col gap-3">
          <div className="max-h-80 overflow-y-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-surface-border text-2xs text-ink-500">
                  <th className="py-1 font-medium">固件</th>
                  <th className="py-1 font-medium">架构</th>
                  <th className="py-1 text-right font-medium">运行</th>
                  <th className="py-1 pl-4 font-medium">Web 可达</th>
                  <th className="py-1 pl-4 text-right font-medium">最近主因</th>
                </tr>
              </thead>
              <tbody>
                {data.firmwares.map((row) => (
                  <tr
                    key={row.image_id ?? 'loose'}
                    className="border-b border-surface-border/60 last:border-0"
                  >
                    <td className="max-w-72 truncate py-1.5 text-ink-100" title={row.label}>
                      {row.label}
                      {row.target_type && (
                        <span className="ml-1.5 text-[10px] text-ink-700">{row.target_type}</span>
                      )}
                    </td>
                    <td className="py-1.5 font-mono text-2xs text-ink-300">{row.arch}</td>
                    <td className="tnum py-1.5 text-right text-ink-300">{row.runs}</td>
                    <td className="py-1.5 pl-4">
                      <span className="flex items-center gap-2">
                        <ArchBar seen={row.runs} ok={row.web_ok} />
                        <span className="tnum shrink-0 text-2xs text-ink-500">
                          {row.web_ok}/{row.runs}
                        </span>
                      </span>
                    </td>
                    <td className="py-1.5 pl-4 text-right text-2xs text-ink-500">
                      {row.last_result_kind ? failureLabel(row.last_result_kind) : DASH}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.unattributed && (
            <div className="flex items-center gap-2 rounded-card border border-surface-border px-3 py-2">
              <Badge tone="warning">未归属</Badge>
              <span className="min-w-0 flex-1 truncate text-2xs text-ink-300">
                {data.unattributed.runs} 次运行没有落到任何已登记固件上，单独计
              </span>
              <span className="tnum shrink-0 text-2xs text-ink-500">
                Web 可达 {data.unattributed.web_ok}/{data.unattributed.runs}
              </span>
            </div>
          )}
          <p className="border-t border-surface-border pt-2 text-[10px] leading-relaxed text-ink-700">
            {data.note}
          </p>
        </div>
      )}
    </Panel>
  )
}

/**
 * How long a run took, per architecture, drawn as a min-to-max span with the median
 * and p90 marked on it.
 *
 * The unmeasured count sits in the header badge rather than in a footnote: the
 * whole reason this panel exists is the difference between "slow" and "never got
 * there", and a reader who only sees the bars cannot tell which library the bars
 * describe. Every figure in it is a time some run actually took -- the server takes
 * nearest-rank percentiles, so no number here is an average of two runs that never
 * happened together.
 */
function LatencyPanel({ query }: { query: ReturnType<typeof useLatency> }) {
  const data = query.data
  return (
    <Panel
      title="Web 可达耗时分布"
      subtitle="跑到 web 面有响应的那些运行，按架构"
      actions={
        <Badge tone={data && data.unmeasured > 0 ? 'warning' : 'neutral'} title="没有耗时记录的运行数">
          未计入 {data ? data.unmeasured : DASH}
        </Badge>
      }
    >
      {query.isLoading && <Skeleton className="h-24 w-full" />}
      {data && data.by_arch.length === 0 && (
        <EmptyState
          title="暂无耗时样本"
          detail="耗时只在 web 面有响应时写入，先跑通一次仿真才会出现在这里"
        />
      )}
      {data && data.by_arch.length > 0 && (
        <div className="flex flex-col gap-3">
          <ul className="flex flex-col gap-3">
            {data.by_arch.map((row) => (
              <li key={row.arch} className="flex flex-col gap-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-mono text-xs text-ink-100">{row.arch}</span>
                  <span className="tnum shrink-0 text-2xs text-ink-500">{row.samples} 次</span>
                </div>
                <SpanBar
                  min={row.min_sec ?? 0}
                  median={row.median_sec ?? 0}
                  p90={row.p90_sec ?? 0}
                  max={row.max_sec ?? 0}
                />
                <div className="flex items-baseline justify-between gap-2 text-[10px] text-ink-700">
                  <span className="tnum">最快 {seconds(row.min_sec, 0)}</span>
                  <span className="tnum text-ink-500">中位 {seconds(row.median_sec, 0)}</span>
                  <span className="tnum">p90 {seconds(row.p90_sec, 0)}</span>
                  <span className="tnum">最长 {seconds(row.max_sec, 0)}</span>
                </div>
                {row.truncated && (
                  <p className="text-[10px] text-ink-700">
                    样本过多，明细只列最近 {row.values.length} 条，次数与极值仍是全量
                  </p>
                )}
              </li>
            ))}
          </ul>
          <p className="border-t border-surface-border pt-2 text-[10px] leading-relaxed text-ink-700">
            {data.note}
          </p>
        </div>
      )}
    </Panel>
  )
}

/**
 * Failures as stage x architecture.
 *
 * Rows are the resolved stage and the signal under it; columns are the
 * architectures, which is the question this table exists to answer -- "is it this
 * firmware, or is it this architecture". Counts come from the same server-side
 * resolution the failure histogram uses and informational kinds are already
 * excluded, so a kind cannot be listed on this board and absent from the one above.
 *
 * A cell with no count shows a dot rather than a zero: the difference between "this
 * combination never happened" and "this combination happened and was fine" is not
 * something a grid should blur.
 */
function FailureMatrixPanel({ query }: { query: ReturnType<typeof useFailureMatrix> }) {
  const data = query.data
  return (
    <Panel
      title="失败矩阵"
      subtitle="归因 × 架构；网络兜底生效等 informational 原因不计为失败"
      actions={
        <Badge
          tone={data && data.unclassified > 0 ? 'warning' : 'neutral'}
          title="无法解析归因阶段的失败信号数"
        >
          未归因 {data ? data.unclassified : DASH}
        </Badge>
      }
    >
      {query.isLoading && <Skeleton className="h-24 w-full" />}
      {data && data.stages.length === 0 && (
        <EmptyState title="暂无失败矩阵" detail="所有已记录的仿真都没有留下失败信号" />
      )}
      {data && data.stages.length > 0 && (
        <div className="flex flex-col gap-3">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-surface-border text-2xs text-ink-500">
                  <th className="py-1 font-medium">归因</th>
                  {data.archs.map((arch) => (
                    <th key={arch} className="py-1 text-right font-mono font-medium">
                      {arch}
                    </th>
                  ))}
                  <th className="py-1 pl-4 text-right font-medium">合计</th>
                </tr>
              </thead>
              <tbody>
                {data.stages.flatMap((stageRow) =>
                  stageRow.cells.map((cell) => (
                    <tr
                      key={`${stageRow.stage}-${cell.kind}`}
                      className="border-b border-surface-border/60 last:border-0"
                    >
                      <td className="max-w-48 truncate py-1.5" title={cell.kind}>
                        <span className="block truncate text-ink-100">
                          {failureLabel(cell.kind)}
                        </span>
                        <span className="block font-mono text-[10px] text-ink-700">
                          {stageRow.stage || DASH}
                        </span>
                      </td>
                      {data.archs.map((arch) => {
                        const count = cell.per_arch[arch]
                        return (
                          <td
                            key={arch}
                            className="tnum py-1.5 text-right text-ink-300"
                          >
                            {count === undefined ? (
                              <span className="text-ink-700">·</span>
                            ) : (
                              count
                            )}
                          </td>
                        )
                      })}
                      <td className="tnum py-1.5 pl-4 text-right text-iris-400">{cell.count}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
          <p className="border-t border-surface-border pt-2 text-[10px] leading-relaxed text-ink-700">
            {data.note}
          </p>
        </div>
      )}
    </Panel>
  )
}
