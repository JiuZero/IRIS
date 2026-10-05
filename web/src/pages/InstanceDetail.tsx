import { useEffect } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { CircleDot, FileText, Network, ScrollText, Terminal as TerminalIcon, Wrench } from 'lucide-react'

import { Badge, Button, DataRow, EmptyState, ErrorState, Panel, Skeleton, StatusDot } from '../components/ui'
import { LaunchOutcome, type LaunchVerdict } from '../components/LaunchDialog'
import { api } from '../lib/api'
import { classNames, DASH, dateTime, failureLabel, layerLabel, seconds, shortDateTime, sinceLabel, sortLayers } from '../lib/format'
import { useConsoleLog, useEmulations } from '../hooks/queries'
import { useTicker } from '../hooks'
import { useUiStore } from '../store/ui'
import type { ActiveEmulation, LayerState, RunDetail, RunsPage } from '../lib/types'

const TABS = [
  { key: 'link', label: '链路探测', icon: Network },
  { key: 'console', label: '串口日志', icon: ScrollText },
  { key: 'detail', label: '运行记录', icon: FileText },
  { key: 'actions', label: '操作', icon: Wrench },
] as const

type TabKey = (typeof TABS)[number]['key']

/**
 * One instance, four tabs.
 *
 * The live instance and the recorded run are shown together because they answer
 * different questions and a demo usually needs both: "is it up right now" (the
 * hosted container) and "what happened to it" (the recorded run, its failures, its
 * repairs). Splitting them across two screens would make the reviewer click back and
 * forth during a three-minute walkthrough.
 */
export function InstanceDetail() {
  const params = useParams()
  const iid = Number.parseInt(params.iid ?? '', 10)
  const [search, setSearch] = useSearchParams()
  const location = useLocation()
  const tab = (search.get('tab') as TabKey | null) ?? 'link'
  const setPinned = useUiStore((state) => state.setPinnedInstance)

  // The launch window hands its verdict over in the location state. Optional by
  // construction: a reload drops it, and this page has to be complete without it --
  // so it is rendered as an extra panel rather than as the source of anything below.
  const launch = (location.state as { launch?: LaunchVerdict } | null)?.launch

  useEffect(() => {
    if (Number.isFinite(iid)) setPinned(iid)
  }, [iid, setPinned])

  const emulations = useEmulations()
  const record = emulations.data?.find((item) => item.iid === iid) ?? null

  // The recorded run is a separate query from the live instance, because it exists
  // for runs that finished long ago and for instances this process never hosted.
  const runs = useQuery({
    queryKey: ['instance-runs', iid],
    queryFn: () => api.runs({ limit: 20, query: String(iid) }),
    enabled: Number.isFinite(iid),
  })
  const runIdParam = search.get('run')
  const detail = useQuery({
    queryKey: ['run-detail', runIdParam],
    queryFn: () => api.run(Number.parseInt(runIdParam ?? '', 10)),
    enabled: runIdParam !== null,
  })

  if (!Number.isFinite(iid)) {
    return (
      <div className="p-4">
        <Panel title="无效的实例号">
          <ErrorState title="地址中缺少实例号" detail="使用 /instances/:iid 形式的地址" />
        </Panel>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <Panel
        title={
          <span className="flex items-center gap-2">
            实例 {iid}
            {record ? (
              <Badge tone={record.web_ok ? 'success' : 'warning'}>
                <StatusDot tone={record.web_ok ? 'success' : 'warning'} pulse={record.web_ok} />
                {record.web_ok ? 'Web 可达' : 'Web 未达'}
              </Badge>
            ) : (
              <Badge tone="neutral">未托管</Badge>
            )}
          </span>
        }
        subtitle={record ? `rootfs ${record.rootfs_path}` : '本进程未托管该实例；下方为历史记录'}
        actions={
          <div className="flex items-center gap-1.5">
            <Link to={`/instances/${iid}/terminal`}>
              <Button size="sm" variant="primary">
                <TerminalIcon className="h-3.5 w-3.5" aria-hidden="true" />
                打开终端
              </Button>
            </Link>
            <Link to={`/instances/${iid}?tab=console`}>
              <Button size="sm" variant="ghost">
                <ScrollText className="h-3.5 w-3.5" aria-hidden="true" />
                日志
              </Button>
            </Link>
          </div>
        }
      >
        <nav aria-label="实例视图" className="segment-group">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              type="button"
              className="segment flex items-center gap-1.5"
              aria-current={tab === key ? 'page' : undefined}
              onClick={() => {
                const next = new URLSearchParams(search)
                next.set('tab', key)
                setSearch(next, { replace: true })
              }}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              {label}
            </button>
          ))}
        </nav>
      </Panel>

      {launch && launch.iid === iid && (
        <Panel
          title="本次启动结果"
          subtitle="由新建实例窗口在跳转时一并带过来；刷新本页后不再显示，完整证据在下方各页签"
          bodyClassName="p-3"
        >
          <LaunchOutcome result={launch} linkToDetail={false} />
        </Panel>
      )}

      {tab === 'link' && <LinkTab iid={iid} record={record} runs={runs.data?.items ?? []} />}
      {tab === 'console' && <ConsoleTab iid={iid} />}
      {tab === 'detail' && <DetailTab detail={detail} runs={runs} />}
      {tab === 'actions' && <ActionsTab iid={iid} record={record} />}
    </div>
  )
}

/**
 * The four-layer link table.
 *
 * `unknown` is drawn differently from `blocked` on purpose. Blocked means a packet
 * stopped there and we know where; unknown means the probe could not run, which is
 * a statement about the measurement, not the network. Rendering both as a red X is
 * how a tool ends up blaming a guest for a docker hiccup.
 */
function LinkTab({
  iid,
  record,
  runs,
}: {
  iid: number
  record: ActiveEmulation | null
  runs: RunsPage['items']
}) {
  const detail = useQuery({
    queryKey: ['run-detail-latest', iid],
    queryFn: () => api.run(runs[0]!.id),
    enabled: runs.length > 0,
  })
  // Ticks only while a hosted instance exists, because the only time-dependent
  // label on this tab is "up for", and that label is meaningless without a start time.
  const tick = useTicker(1_000, Boolean(record))

  const link = detail.data?.link
  // The gate is `link` itself rather than a derived `rows` variable: TypeScript narrows
  // the union inside `{link && ...}`, and it cannot narrow it through a second variable
  // that merely happens to be computed from it.
  const hasLayers = link !== null && link !== undefined && link.layers.length > 0

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <Panel title="四层链路" subtitle={link ? `guest ${link.guest_ip}` : '最近一次记录的链路结论'}>
        {detail.isLoading && <Skeleton className="h-40 w-full" />}
        {!detail.isLoading && !hasLayers && (
          <EmptyState
            title="这次运行没有链路摘要"
            detail="链路表只在四层出现阻断且探测跑完时落库摘要；全通或探测不可用的运行不记证据，看到空表不是「没问题」，是没有这次测量"
          />
        )}
        {link && hasLayers && (
          <>
            <ul className="flex flex-col gap-1.5">
              {sortLayers(link.layers).map((row) => (
                <li
                  key={row.layer}
                  className={classNames(
                    'flex items-start gap-2 rounded-card border px-2 py-1.5',
                    row.state === 'ok'
                      ? 'border-success/25 bg-success/5'
                      : row.state === 'blocked'
                        ? 'border-danger/25 bg-danger/5'
                        : 'border-surface-border bg-surface-faint',
                  )}
                >
                  <StatusDot
                    tone={row.state === 'ok' ? 'success' : row.state === 'blocked' ? 'danger' : 'neutral'}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-ink-100">{layerLabel(row.layer)}</span>
                      <span className="font-mono text-[10px] text-ink-700">{row.layer}</span>
                      <StateBadge state={row.state} />
                    </div>
                    {row.detail && (
                      <p className="mt-0.5 break-words text-[10px] leading-relaxed text-ink-500">{row.detail}</p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
            {link.first_break && (
              <p className="mt-2 border-t border-surface-border pt-2 text-2xs text-danger">
                首个断点：{failureLabel(link.first_break)}
              </p>
            )}
            {link.unavailable && (
              <p className="mt-1 text-2xs text-ink-700">探测不可用：{link.unavailable}</p>
            )}
            {link.note && (
              <p className="mt-2 border-t border-surface-border pt-2 text-[10px] leading-relaxed text-ink-700">
                {link.note}
              </p>
            )}
          </>
        )}
      </Panel>

      <Panel title="实例概况">
        {!record ? (
          <EmptyState
            title="本进程未托管该实例"
            detail="命令行启动的仿真不写入 API 的托管表，因此页面无法读取它的实时状态"
          />
        ) : (
          <>
            <DataRow label="架构" mono>
              {record.arch || DASH}
            </DataRow>
            <DataRow label="容器" mono>
              {record.container_id || DASH}
            </DataRow>
            <DataRow label="Web 地址" mono>
              {record.web_url || DASH}
            </DataRow>
            <DataRow label="启动于" mono>
              {shortDateTime(record.started_at)}
            </DataRow>
            <DataRow label="已运行" mono>
              {sinceLabel(record.started_at, tick)}
            </DataRow>
            <DataRow label="归属">{record.client_id}</DataRow>
          </>
        )}
      </Panel>
    </div>
  )
}

function StateBadge({ state }: { state: LayerState }) {
  if (state === 'ok') return <Badge tone="success">通</Badge>
  if (state === 'blocked') return <Badge tone="danger">阻断</Badge>
  return <Badge tone="neutral">未探测</Badge>
}

/**
 * The console log, read from the snapshot the orchestrator copies back.
 *
 * Not the terminal view, and the page says so: the snapshot is what the failure
 * diagnosis read, so it is authoritative for "what the guest printed", but it is
 * written when the run ends and contains no terminal input. The live stream is the
 * terminal tab, over the websocket.
 */
function ConsoleTab({ iid }: { iid: number }) {
  const log = useConsoleLog(iid, true)

  return (
    <Panel
      title="串口日志快照"
      subtitle="运行结束时由编排器回写到宿主；不含你在终端里输入的内容"
      actions={<Badge tone={log.data?.available ? 'iris' : 'neutral'}>{log.data?.total_lines ?? 0} 行</Badge>}
      bodyClassName="p-0"
    >
      {log.isLoading && <div className="p-3"><Skeleton className="h-64 w-full" /></div>}
      {log.isError && <ErrorState title="无法读取日志" detail={(log.error as Error).message} />}
      {log.data?.available === false && (
        <EmptyState title="没有该实例的日志快照" detail={log.data.reason ?? '快照在运行结束时才落盘'} />
      )}
      {log.data?.available && (
        <>
          <pre className="scroll-y max-h-[60vh] whitespace-pre-wrap break-words bg-surface-code p-3 font-mono text-[11px] leading-relaxed text-ink-300">
            {log.data.lines.join('\n') || '(空)'}
          </pre>
          <p className="border-t border-surface-border px-3 py-1.5 text-[10px] text-ink-700">
            {log.data.complete ? '已显示全部内容' : `仅显示前 ${log.data.lines.length} 行（共 ${log.data.total_lines} 行）`}
            {' · '}
            <span className="font-mono">{log.data.path}</span>
          </p>
        </>
      )}
    </Panel>
  )
}

function DetailTab({
  detail,
  runs,
}: {
  detail: UseQueryResult<RunDetail, Error>
  runs: UseQueryResult<RunsPage, Error>
}) {
  const rows = runs.data?.items ?? []
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <Panel title="该 iid 的历史运行" bodyClassName="p-0">
        {runs.isLoading && <div className="p-3"><Skeleton className="h-32 w-full" /></div>}
        {rows.length === 0 && <EmptyState title="没有记录" detail="这个 iid 在库里没有仿真记录" />}
        {rows.length > 0 && (
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-surface-border text-2xs text-ink-500">
                <th className="px-3 py-2 font-medium">记录</th>
                <th className="px-3 py-2 font-medium">Web</th>
                <th className="px-3 py-2 font-medium">耗时</th>
                <th className="px-3 py-2 font-medium">结论</th>
                <th className="px-3 py-2 font-medium">开始</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b border-surface-border/60 last:border-0">
                  <td className="px-3 py-2 font-mono text-ink-300">#{row.id}</td>
                  <td className="px-3 py-2">
                    {row.web_ok === null ? (
                      <span className="text-ink-700">未探测</span>
                    ) : row.web_ok ? (
                      <Badge tone="success">可达</Badge>
                    ) : (
                      <Badge tone="danger">不可达</Badge>
                    )}
                  </td>
                  <td className="tnum px-3 py-2 text-ink-300">{seconds(row.time_web)}</td>
                  <td className="px-3 py-2 text-ink-300">
                    {row.result_kind ? failureLabel(row.result_kind) : DASH}
                  </td>
                  <td className="px-3 py-2 text-2xs text-ink-500">{shortDateTime(row.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="选中记录的失败与修复" subtitle="失败信号逐条列出，修复动作带证据">
        {detail.isLoading && <Skeleton className="h-40 w-full" />}
        {detail.isError && <ErrorState title="无法读取记录" detail={(detail.error as Error).message} />}
        {detail.data && (
          <>
            <DataRow label="开始" mono>
              {dateTime(detail.data.started_at)}
            </DataRow>
            <DataRow label="结束" mono>
              {dateTime(detail.data.finished_at)}
            </DataRow>
            <div className="mt-2">
              <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-700">失败信号</h3>
              {detail.data.failures.length === 0 && <p className="py-1 text-2xs text-ink-700">无</p>}
              <ul className="mt-1 flex flex-col gap-1">
                {detail.data.failures.map((failure, index) => (
                  <li key={`${failure.stage}-${index}`} className="rounded-card border border-surface-border px-2 py-1">
                    <div className="flex items-center gap-2 text-2xs">
                      <Badge tone="danger">{failure.stage || '—'}</Badge>
                      <span className="text-ink-100">{failureLabel(failure.signal)}</span>
                    </div>
                    {failure.log_fingerprint && (
                      <p className="mt-0.5 break-all font-mono text-[10px] text-ink-700">{failure.log_fingerprint}</p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
            <div className="mt-2">
              <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-700">修复动作</h3>
              {detail.data.repairs.length === 0 && <p className="py-1 text-2xs text-ink-700">无</p>}
              <ul className="mt-1 flex flex-col gap-1">
                {detail.data.repairs.map((repair, index) => (
                  <li key={`${repair.rule_id}-${index}`} className="rounded-card border border-surface-border px-2 py-1">
                    <div className="flex items-center gap-2 text-2xs">
                      <Badge tone={repair.applied ? 'success' : 'neutral'}>{repair.applied ? '已应用' : '未应用'}</Badge>
                      <span className="font-mono text-ink-100">{repair.rule_id || repair.source}</span>
                    </div>
                    {repair.evidence && <p className="mt-0.5 text-[10px] text-ink-700">{repair.evidence}</p>}
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}
        {!detail.data && !detail.isLoading && (
          <EmptyState
            title="未选中记录"
            detail="从「实例记录」页打开某条记录，或在上表点一条记录（地址形如 /instances/:iid?run=编号）"
          />
        )}
      </Panel>
    </div>
  )
}

function ActionsTab({
  iid,
  record,
}: {
  iid: number
  record: { container_id: string; web_url: string } | null
}) {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <Panel title="停止">
        <p className="text-2xs leading-relaxed text-ink-500">
          停止会删除容器并从托管表移除该行，此动作不可撤销，页面上没有撤销入口，
          停止之后要重新跑一次仿真
        </p>
        <div className="mt-2">
          <Link to="/instances">
            <Button variant="danger">前往实例列表执行停止</Button>
          </Link>
        </div>
      </Panel>
      <Panel title="外部入口">
        <DataRow label="Web 地址" mono>
          {record?.web_url || DASH}
        </DataRow>
        <DataRow label="容器 ID" mono>
          {record?.container_id || DASH}
        </DataRow>
        <p className="mt-2 text-[10px] leading-relaxed text-ink-700">
          guest 的 Web 服务由 QEMU 内的网络转发到宿主端口，串口只在回环地址上发布，
          页面通过本服务的 WebSocket 接入，不直接连 guest
        </p>
        <p className="mt-2 flex items-center gap-1 text-[10px] text-ink-700">
          <CircleDot className="h-3 w-3" aria-hidden="true" />
          iid {iid}
        </p>
      </Panel>
    </div>
  )
}