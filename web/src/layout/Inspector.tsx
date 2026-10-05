import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Activity, Boxes, Cpu, FolderTree, ScrollText, Wrench } from 'lucide-react'

import { Badge, DataRow, EmptyState, ErrorState, Skeleton, StatusDot } from '../components/ui'
import { classNames, DASH, megabytes, percentPoints, sinceLabel } from '../lib/format'
import { useCapabilities, useConsoleLog, useConfig, useEmulations, useInstanceStats, useRootCauses } from '../hooks/queries'
import { useTicker } from '../hooks'

/**
 * Six read-only panels, always in the same order.
 *
 * All six answer "what is this build actually doing", and none of them can change
 * anything. That is deliberate: an inspector that could stop an instance would be a
 * second place to destroy state, and the pages that own those actions are the ones
 * that ask for a confirmation.
 *
 * Each panel states its own honesty limit rather than leaving the reader to infer
 * it -- an empty console panel means "the run has not finished", which is a
 * different fact from "nothing was captured".
 */
export function Inspector({ iid }: { iid: number | null }) {
  // Read once here and handed to both panels that need it. They were each asking on
  // their own, and worse: a query mounted twice is still one cache entry, so the
  // second panel inherited whatever the first had rendered -- which is how a stopped
  // instance kept showing a container's CPU next to the list that no longer had it.
  const stats = useInstanceStats(iid)
  const state = stats.data?.state

  return (
    <aside
      aria-label="检查器"
      className="scroll-y flex h-full min-h-0 flex-col gap-3 border-l border-surface-border bg-surface-sunken/60 p-3"
    >
      <InstancePanel iid={iid} />
      <ResourcePanel iid={iid} query={stats} />
      <ConsolePanel iid={iid} state={state} />
      <CorpusPanel />
      <KnowledgePanel />
      <BuildPanel />
    </aside>
  )
}

function Section({
  icon,
  title,
  children,
  aside,
}: {
  icon: React.ReactNode
  title: string
  children: React.ReactNode
  aside?: React.ReactNode
}) {
  return (
    <section className="glass-soft flex flex-col gap-1.5 p-3">
      <header className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-500">
          <span className="text-iris-400">{icon}</span>
          {title}
        </h3>
        {aside}
      </header>
      {children}
    </section>
  )
}

function InstancePanel({ iid }: { iid: number | null }) {
  const emulations = useEmulations()
  const record = useMemo(
    () => (emulations.data ?? []).find((item) => item.iid === iid) ?? null,
    [emulations.data, iid],
  )
  const tick = useTicker(1_000, Boolean(record))

  if (iid === null) {
    return (
      <Section icon={<Boxes className="h-3.5 w-3.5" aria-hidden="true" />} title="实例">
        <EmptyState title="未选择实例" detail="打开一个实例后，这里显示它的容器与归属信息" />
      </Section>
    )
  }
  if (!record) {
    return (
      <Section icon={<Boxes className="h-3.5 w-3.5" aria-hidden="true" />} title={`实例 ${iid}`}>
        <EmptyState
          title="此实例不在本进程的托管列表中"
          detail="命令行启动的仿真不经过 API，页面无法停止它或接入它的终端"
          action={
            <Link to="/instances" className="text-2xs text-iris-400 hover:underline">
              查看实例列表
            </Link>
          }
        />
      </Section>
    )
  }
  return (
    <Section
      icon={<Boxes className="h-3.5 w-3.5" aria-hidden="true" />}
      title={`实例 ${record.iid}`}
      aside={
        <Badge tone={record.web_ok ? 'success' : 'warning'}>
          <StatusDot tone={record.web_ok ? 'success' : 'warning'} pulse={record.web_ok} />
          {record.web_ok ? 'Web 可达' : 'Web 未达'}
        </Badge>
      }
    >
      <DataRow label="架构" mono>
        {record.arch || DASH}
      </DataRow>
      <DataRow label="容器" mono>
        {record.container_id.slice(0, 12) || DASH}
      </DataRow>
      <DataRow label="rootfs" mono>
        {record.rootfs_path || DASH}
      </DataRow>
      <DataRow label="Web 地址" mono>
        {record.web_url || DASH}
      </DataRow>
      <DataRow label="已运行" mono>
        {sinceLabel(record.started_at, tick)}
      </DataRow>
    </Section>
  )
}

function ResourcePanel({
  iid,
  query,
}: {
  iid: number | null
  query: ReturnType<typeof useInstanceStats>
}) {
  if (iid === null) {
    return (
      <Section icon={<Cpu className="h-3.5 w-3.5" aria-hidden="true" />} title="资源占用">
        <EmptyState title="未选择实例" />
      </Section>
    )
  }
  if (query.isLoading) {
    return (
      <Section icon={<Cpu className="h-3.5 w-3.5" aria-hidden="true" />} title="资源占用">
        <Skeleton className="h-14 w-full" />
      </Section>
    )
  }
  // The title used to claim docker was the problem. It never was: the request failed
  // because the caller was not allowed to read this instance, and every failure here
  // looked like a monitoring outage on the host.
  if (query.isError) {
    return (
      <Section icon={<Cpu className="h-3.5 w-3.5" aria-hidden="true" />} title="资源占用">
        <ErrorState title="读不到该实例的资源占用" detail={(query.error as Error).message} />
      </Section>
    )
  }
  // A terminal state, not an error: the instance was stopped and the answer will not
  // change again. Saying so is what lets the reader tell it apart from a container
  // that is merely quiet.
  if (query.data?.state === 'gone') {
    return (
      <Section icon={<Cpu className="h-3.5 w-3.5" aria-hidden="true" />} title="资源占用">
        <EmptyState
          title="实例已停止"
          detail="容器已删除，不再有 CPU 或内存读数；串口快照仍保留在下方"
          action={
            <Link to="/instances" className="text-2xs text-iris-400 hover:underline">
              查看实例列表
            </Link>
          }
        />
      </Section>
    )
  }
  const data = query.data
  return (
    <Section
      icon={<Cpu className="h-3.5 w-3.5" aria-hidden="true" />}
      title="资源占用"
      aside={<Badge tone={data?.sampled ? 'cyan' : 'neutral'}>{data?.sampled ? '实时采样' : '未采样'}</Badge>}
    >
      {/* A dash, never 0%: the server distinguishes "not measured" from "measured
          and idle", and collapsing that here would make a starting container look
          like an idle one. */}
      <DataRow label="CPU" mono>
        {percentPoints(data?.cpu_pct ?? null)}
      </DataRow>
      <DataRow label="内存" mono>
        {megabytes(data?.mem_mb ?? null)}
      </DataRow>
      <DataRow label="内存上限" mono>
        {megabytes(data?.mem_limit_mb ?? null)}
      </DataRow>
      <DataRow label="串口端口" mono>
        {data?.serial_port ?? DASH}
      </DataRow>
    </Section>
  )
}

function ConsolePanel({
  iid,
  state,
}: {
  iid: number | null
  state: 'running' | 'gone' | undefined
}) {
  const log = useConsoleLog(iid, iid !== null)

  if (iid === null) {
    return (
      <Section icon={<ScrollText className="h-3.5 w-3.5" aria-hidden="true" />} title="串口快照">
        <EmptyState title="未选择实例" />
      </Section>
    )
  }
  if (log.isError) {
    return (
      <Section icon={<ScrollText className="h-3.5 w-3.5" aria-hidden="true" />} title="串口快照">
        <ErrorState title="读不到串口快照" detail={(log.error as Error).message} />
      </Section>
    )
  }
  return (
    <Section
      icon={<ScrollText className="h-3.5 w-3.5" aria-hidden="true" />}
      title="串口快照"
      aside={
        <Badge tone={log.data?.available ? (state === 'gone' ? 'neutral' : 'iris') : 'neutral'}>
          {log.data?.available ? (state === 'gone' ? '已归档' : '已捕获') : '无'}
        </Badge>
      }
    >
      {log.isLoading && <Skeleton className="h-12 w-full" />}
      {log.data?.available === false && (
        <p className="text-2xs leading-relaxed text-ink-500">
          {log.data.reason ?? '没有快照'}
          <br />
          <span className="text-ink-700">快照在运行结束时落盘；运行中的输出走终端通道</span>
        </p>
      )}
      {log.data?.available && (
        <>
          <pre className="scroll-y max-h-24 whitespace-pre-wrap break-all rounded-card bg-surface-code p-2 font-mono text-[10px] leading-relaxed text-ink-300">
            {log.data.lines.slice(-12).join('\n') || '(空)'}
          </pre>
          <p className="text-[10px] text-ink-700">
            共 {log.data.total_lines ?? log.data.lines.length} 行 · 此处不包含终端输入
          </p>
          {/* The snapshot outlives the container on purpose -- it is the evidence a
              failure diagnosis reads -- so it stays on screen after a stop. What has
              to change is what it claims to be: a panel of past output next to a
              live CPU reading reads as a live console. */}
          {state === 'gone' && (
            <p className="text-[10px] leading-relaxed text-ink-700">
              这是已停止实例的历史快照，不会再增长
              {iid !== null && (
                <>
                  {' · '}
                  <Link to={`/instances/${iid}?tab=console`} className="text-iris-400 hover:underline">
                    查看完整日志
                  </Link>
                </>
              )}
            </p>
          )}
        </>
      )}
    </Section>
  )
}

function CorpusPanel() {
  const config = useConfig()
  return (
    <Section icon={<FolderTree className="h-3.5 w-3.5" aria-hidden="true" />} title="语料与配置">
      {config.isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : (
        <>
          <DataRow label="数据库" mono>
            {config.data?.database_url ?? DASH}
          </DataRow>
          <DataRow label="暂存目录" mono>
            {config.data?.scratch_dir ?? DASH}
          </DataRow>
          <DataRow label="上传上限" mono>
            {config.data ? `${config.data.api_max_upload_mb} MB` : DASH}
          </DataRow>
        </>
      )}
      <p className="text-[10px] leading-relaxed text-ink-700">
        配置只读，来自 .env 与 IRIS_* 环境变量，修改后需重启 iris web
      </p>
    </Section>
  )
}

function KnowledgePanel() {
  const causes = useRootCauses(5)
  if (causes.isLoading) {
    return (
      <Section icon={<Wrench className="h-3.5 w-3.5" aria-hidden="true" />} title="失败知识">
        <Skeleton className="h-12 w-full" />
      </Section>
    )
  }
  const cards = (causes.data?.cards ?? []).slice(0, 4)
  return (
    <Section
      icon={<Wrench className="h-3.5 w-3.5" aria-hidden="true" />}
      title="失败知识"
      aside={<Badge tone="neutral">{cards.length}</Badge>}
    >
      {cards.length === 0 && <p className="text-2xs text-ink-700">尚无失败聚类</p>}
      <ul className="flex flex-col gap-1">
        {cards.map((card) => (
          <li key={`${card.stage}-${card.kind}`} className="flex items-center gap-2 text-2xs">
            <StatusDot tone={card.candidate ? 'warning' : 'neutral'} />
            <span className="flex-1 truncate text-ink-300">{card.kind}</span>
            <span className="tnum text-ink-500">{card.runs} 次</span>
          </li>
        ))}
      </ul>
    </Section>
  )
}

function BuildPanel() {
  const capabilities = useCapabilities()
  if (capabilities.isLoading) {
    return (
      <Section icon={<Activity className="h-3.5 w-3.5" aria-hidden="true" />} title="能力矩阵">
        <Skeleton className="h-12 w-full" />
      </Section>
    )
  }
  return (
    <Section icon={<Activity className="h-3.5 w-3.5" aria-hidden="true" />} title="能力矩阵">
      <ul className="flex flex-col gap-1">
        {(capabilities.data?.items ?? []).map((item) => (
          <li key={item.id} className="flex items-center gap-2 text-2xs" title={item.detail}>
            <StatusDot
              tone={item.state === 'available' ? 'success' : item.state === 'planned' ? 'warning' : 'danger'}
            />
            <span className="flex-1 truncate text-ink-300">{item.name}</span>
            <span className={classNames('text-[10px]', item.state === 'available' ? 'text-ink-700' : 'text-warning')}>
              {item.state === 'available' ? '就绪' : item.state === 'planned' ? '待改造' : '不含模型'}
            </span>
          </li>
        ))}
      </ul>
      <p className="text-[10px] leading-relaxed text-ink-700">
        状态由后端探测构建内容得出，不是写死的文案
      </p>
    </Section>
  )
}

