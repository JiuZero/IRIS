import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { FileDown, Plus, RefreshCw, Square, Terminal as TerminalIcon } from 'lucide-react'

import { Badge, Button, EmptyState, ErrorState, Panel, Select, Skeleton, StatusDot, TextInput } from '../components/ui'
import { api } from '../lib/api'
import { classNames, DASH, failureLabel, seconds, shortDateTime } from '../lib/format'
import { useEmulations } from '../hooks/queries'
import type { RunsPage } from '../lib/types'

const PAGE_SIZE = 20

/**
 * The instance record: the runs this service is hosting, and the historical runs
 * it has recorded.
 *
 * Two tables, because they answer different questions and have different lifetimes.
 * The active table changes when a container starts and stops; the history table
 * only ever grows. Mixing them into one list would mean a row's meaning depends on
 * where you looked.
 *
 * Nothing here creates an instance. Creating one is a launch -- three sources of
 * firmware, a port, a timeout, and a boot that takes tens of seconds -- and it
 * belongs on the dashboard next to the environment reading that decides whether a
 * launch is a good idea right now. A page of records is for reading records.
 */
export function Instances() {
  const emulations = useEmulations()
  const [arch, setArch] = useState('')
  const [kind, setKind] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)

  const history = useQuery({
    queryKey: ['runs', arch, kind, query, page],
    queryFn: () => api.runs({ limit: PAGE_SIZE, offset: page * PAGE_SIZE, arch, result_kind: kind, query }),
  })

  const kinds = useMemo(() => {
    const seen = new Set<string>()
    for (const row of history.data?.items ?? []) if (row.result_kind) seen.add(row.result_kind)
    return [...seen].sort()
  }, [history.data])

  return (
    <div className="flex flex-col gap-4 p-4">
      <ActiveTable query={emulations} />
      <HistoryTable
        arch={arch}
        kind={kind}
        query={query}
        page={page}
        kinds={kinds}
        onArch={setArch}
        onKind={setKind}
        onQuery={(value) => {
          setQuery(value)
          setPage(0)
        }}
        onPage={setPage}
        result={history}
      />
    </div>
  )
}

function ActiveTable({ query }: { query: ReturnType<typeof useEmulations> }) {
  const client = useQueryClient()
  const stop = useMutation({
    mutationFn: (iid: number) => api.stopEmulation(iid),
    onSuccess: () => {
      // Both caches, because a stopped container must disappear from the sidebar too.
      void client.invalidateQueries({ queryKey: ['emulations'] })
      void client.invalidateQueries({ queryKey: ['stats'] })
    },
  })

  return (
    <Panel
      title="本服务托管的实例"
      subtitle="只有从这里启动的仿真能被页面停止或接入终端"
      actions={
        <Button size="sm" variant="ghost" onClick={() => void query.refetch()} title="刷新">
          <RefreshCw className={classNames('h-3.5 w-3.5', query.isFetching && 'animate-spin')} aria-hidden="true" />
          刷新
        </Button>
      }
      bodyClassName="p-0"
    >
      {query.isLoading && <div className="p-3"><Skeleton className="h-16 w-full" /></div>}
      {query.isError && (
        <ErrorState title="无法读取实例列表" detail={(query.error as Error).message} onRetry={() => void query.refetch()} />
      )}
      {query.data?.length === 0 && (
        <EmptyState
          title="当前没有运行中的实例"
          detail="命令行启动的实例不受页面管理；本页只列出本服务托管的那些"
          action={
            <Link to="/#launch">
              <Button size="sm" variant="outline">
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                去总览页新建实例
              </Button>
            </Link>
          }
        />
      )}
      {query.data && query.data.length > 0 && (
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-surface-border text-2xs text-ink-500">
              <th className="px-3 py-2 font-medium">实例</th>
              <th className="px-3 py-2 font-medium">架构</th>
              <th className="px-3 py-2 font-medium">状态</th>
              <th className="px-3 py-2 font-medium">Web</th>
              <th className="px-3 py-2 font-medium">容器</th>
              <th className="px-3 py-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {query.data.map((item) => (
              <tr key={item.iid} className="border-b border-surface-border/60 last:border-0 hover:bg-surface-faint">
                <td className="px-3 py-2">
                  <Link
                    to={`/instances/${item.iid}`}
                    className="font-mono text-xs text-iris-400 hover:underline"
                  >
                    {item.iid}
                  </Link>
                </td>
                <td className="px-3 py-2 font-mono text-ink-300">{item.arch || DASH}</td>
                <td className="px-3 py-2">
                  <Badge tone={item.success ? 'success' : 'warning'}>
                    <StatusDot tone={item.success ? 'success' : 'warning'} pulse={item.success} />
                    {item.success ? '仿真成功' : '未成功'}
                  </Badge>
                </td>
                <td className="px-3 py-2">
                  {item.web_url ? (
                    <a
                      href={item.web_url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="font-mono text-2xs text-cyan hover:underline"
                    >
                      {item.web_url}
                    </a>
                  ) : (
                    <span className="text-ink-700">{DASH}</span>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-2xs text-ink-700">
                  {item.container_id.slice(0, 12) || DASH}
                </td>
                <td className="px-3 py-2">
                  <div className="flex items-center justify-end gap-1.5">
                    <Link to={`/instances/${item.iid}/terminal`}>
                      <Button size="sm" variant="outline">
                        <TerminalIcon className="h-3.5 w-3.5" aria-hidden="true" />
                        终端
                      </Button>
                    </Link>
                    <Button
                      size="sm"
                      variant="danger"
                      disabled={stop.isPending}
                      onClick={() => stop.mutate(item.iid)}
                      title="停止并删除容器"
                    >
                      <Square className="h-3 w-3" aria-hidden="true" />
                      停止
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {stop.isError && (
        <p className="border-t border-surface-border px-3 py-2 text-2xs text-danger" role="alert">
          停止失败：{(stop.error as Error).message}
        </p>
      )}
    </Panel>
  )
}

function HistoryTable({
  arch,
  kind,
  query,
  page,
  kinds,
  onArch,
  onKind,
  onQuery,
  onPage,
  result,
}: {
  arch: string
  kind: string
  query: string
  page: number
  kinds: string[]
  onArch: (value: string) => void
  onKind: (value: string) => void
  onQuery: (value: string) => void
  onPage: (value: number) => void
  result: UseQueryResult<RunsPage, Error>
}) {
  const total = result.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <Panel
      title="历史运行记录"
      subtitle="全库累计；筛选在服务端执行，分页计数与筛选一致"
      actions={<ExportButton />}
      bodyClassName="p-0"
    >
      <div className="flex flex-wrap items-end gap-2 border-b border-surface-border px-3 py-2">
        <TextInput
          label="搜索"
          placeholder="iid 或 IP 片段"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          className="w-40"
        />
        <Select label="架构" value={arch} onChange={(event) => onArch(event.target.value)}>
          <option value="">全部</option>
          {['mipsel', 'mipseb', 'armel', 'arm64'].map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </Select>
        <Select label="失败类型" value={kind} onChange={(event) => onKind(event.target.value)}>
          <option value="">全部</option>
          {kinds.map((value) => (
            <option key={value} value={value}>
              {failureLabel(value)}
            </option>
          ))}
        </Select>
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="sm" variant="ghost" disabled={page === 0} onClick={() => onPage(page - 1)}>
            上一页
          </Button>
          <span className="tnum text-2xs text-ink-500">
            第 {page + 1} / {pages} 页 · 共 {total} 条
          </span>
          <Button size="sm" variant="ghost" disabled={page + 1 >= pages} onClick={() => onPage(page + 1)}>
            下一页
          </Button>
        </div>
      </div>

      {result.isLoading && <div className="p-3"><Skeleton className="h-40 w-full" /></div>}
      {result.isError && (
        <ErrorState title="无法读取运行记录" detail={(result.error as Error).message} onRetry={() => void result.refetch()} />
      )}
      {result.data?.items.length === 0 && (
        <EmptyState
          title="没有匹配的记录"
          detail="全库中没有符合当前筛选的运行记录；放宽条件或翻页即可看到更早的记录"
        />
      )}
      {result.data && result.data.items.length > 0 && (
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-surface-border text-2xs text-ink-500">
              <th className="px-3 py-2 font-medium">记录</th>
              <th className="px-3 py-2 font-medium">iid</th>
              <th className="px-3 py-2 font-medium">架构</th>
              <th className="px-3 py-2 font-medium">Web</th>
              <th className="px-3 py-2 font-medium">耗时</th>
              <th className="px-3 py-2 font-medium">结论</th>
              <th className="px-3 py-2 font-medium">开始</th>
            </tr>
          </thead>
          <tbody>
            {result.data.items.map((row) => (
              <tr key={row.id} className="border-b border-surface-border/60 last:border-0 hover:bg-surface-faint">
                <td className="px-3 py-2">
                  <Link to={`/instances/${row.iid}?run=${row.id}`} className="font-mono text-xs text-iris-400 hover:underline">
                    #{row.id}
                  </Link>
                </td>
                <td className="px-3 py-2 font-mono text-ink-300">{row.iid}</td>
                <td className="px-3 py-2 font-mono text-ink-300">{row.arch || DASH}</td>
                <td className="px-3 py-2">
                  {/* Three states, not two: `null` means the run ended before the
                      web probe answered, which is not the same as a refusal. */}
                  {row.web_ok === null ? (
                    <span className="text-ink-700" title="未探测">
                      未探测
                    </span>
                  ) : row.web_ok ? (
                    <Badge tone="success">可达 {row.time_web ? `${row.time_web}s` : ''}</Badge>
                  ) : (
                    <Badge tone="danger">不可达</Badge>
                  )}
                </td>
                <td className="tnum px-3 py-2 text-ink-300">{seconds(row.time_web)}</td>
                <td className="px-3 py-2">
                  {row.result_kind ? (
                    <Badge tone={row.result_kind.startsWith('link-no') ? 'warning' : 'danger'}>
                      {failureLabel(row.result_kind)}
                    </Badge>
                  ) : (
                    <span className="text-ink-700">{DASH}</span>
                  )}
                </td>
                <td className="px-3 py-2 text-2xs text-ink-500">{shortDateTime(row.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="border-t border-surface-border px-3 py-2 text-[10px] text-ink-700">
        上表只显示当前筛选下的一页。统计卡与评测集使用全库口径，两处数字来自同一份聚合，
        不随分页或筛选变化
      </p>
    </Panel>
  )
}

function ExportButton() {
  const [state, setState] = useState<'idle' | 'busy' | 'done' | 'error'>('idle')
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={state === 'busy'}
      onClick={() => {
        setState('busy')
        api
          .exportCsv()
          .then((blob) => {
            const url = URL.createObjectURL(blob)
            const anchor = document.createElement('a')
            anchor.href = url
            anchor.download = 'iris-runs.csv'
            anchor.click()
            URL.revokeObjectURL(url)
            setState('done')
          })
          .catch(() => setState('error'))
      }}
    >
      <FileDown className="h-3.5 w-3.5" aria-hidden="true" />
      {state === 'busy' ? '导出中…' : state === 'done' ? '已导出' : state === 'error' ? '导出失败' : '导出 CSV'}
    </Button>
  )
}

