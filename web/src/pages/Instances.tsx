import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowRight,
  BrainCircuit,
  Eraser,
  FileDown,
  Plus,
  RefreshCw,
  Square,
  Terminal as TerminalIcon,
  Trash2,
} from 'lucide-react'

import { Modal } from '../components/Modal'
import { RunRecord } from '../components/RunRecord'
import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  Panel,
  Select,
  Skeleton,
  StatusDot,
  TextInput,
} from '../components/ui'
import type { Tone } from '../components/ui'
import { ApiError, api } from '../lib/api'
import { classNames, DASH, failureLabel, runStateLabel, runStateTone, seconds, shortDateTime } from '../lib/format'
import { useDiagnose, useEmulations, useSaveDraft } from '../hooks/queries'
import { useUiStore } from '../store/ui'
import type { LlmAction, PluginDraft, RunsPage, RunItem } from '../lib/types'

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
 * A row is also the entry to that run's evidence. The table answers "what happened",
 * which is five columns and a verdict badge; everything that explains *why* -- the
 * four-layer profile, the failure rows, the repair ledger -- is one window away,
 * because a summary you cannot open is a claim you cannot check. And history has to
 * be erasable: a corpus that has been re-run sixty times on one image makes every
 * cumulative statistic unreadable, so there is a per-row delete and a bulk clear,
 * both behind a confirmation that says how much is about to go.
 */
export function Instances() {
  const emulations = useEmulations()
  const [arch, setArch] = useState('')
  const [kind, setKind] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const openLaunch = useUiStore((state) => state.openLaunch)

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
      <ActiveTable query={emulations} onLaunch={openLaunch} />
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

function ActiveTable({
  query,
  onLaunch,
}: {
  query: ReturnType<typeof useEmulations>
  onLaunch: () => void
}) {
  const client = useQueryClient()
  const pinned = useUiStore((state) => state.pinnedInstance)
  const setPinned = useUiStore((state) => state.setPinnedInstance)
  const [diagnosing, setDiagnosing] = useState<number | null>(null)
  const stop = useMutation({
    mutationFn: (iid: number) => api.stopEmulation(iid),
    onSuccess: (_result, iid) => {
      // Both lists, because a stopped container must disappear from the sidebar too.
      void client.invalidateQueries({ queryKey: ['emulations'] })
      void client.invalidateQueries({ queryKey: ['stats'] })
      // This one's resource reading is dropped rather than invalidated: there is no
      // longer an instance behind that key, and invalidating would fire one more
      // request at it. The console snapshot is the exception -- it is written when the
      // run ends, so re-reading it is how the panel picks up the final lines.
      client.removeQueries({ queryKey: ['instance-stats', iid] })
      if (pinned === iid) setPinned(null)
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
            <Button size="sm" variant="outline" onClick={onLaunch}>
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              新建实例
            </Button>
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
                      variant="outline"
                      onClick={() => setDiagnosing(item.iid)}
                      title="AI 诊断：先读串口日志模式计数，未命中才问模型"
                    >
                      <BrainCircuit className="h-3.5 w-3.5" aria-hidden="true" />
                      AI 诊断
                    </Button>
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
      <DiagnosisModal iid={diagnosing} onClose={() => setDiagnosing(null)} />
    </Panel>
  )
}

/** The diagnosis window: what one instance's failure looks like, and who said so.
 *
 *  The three outcomes are rendered differently, because they are three different
 *  facts and flattening them would make the rules engine's free answer look like a
 *  failure: a `rule_recommendation` means the deterministic layer already diagnosed
 *  it and no token was spent; `disabled_reason` means nothing ran because no endpoint
 *  is configured; `error` means a call was attempted and lost. `llm_used` alone
 *  cannot carry that distinction, so the panel keys off the fields that do.
 *
 *  Nothing here executes. A restart is a suggestion with a button that does not
 *  exist yet on purpose -- the one repair that reaches a running guest is a container
 *  restart, and it is the operator's call. A draft is saved by a button and reviewed
 *  on the plugin page, which is the whole safety story: the model proposes, a person
 *  disposes. */
function DiagnosisModal({ iid, onClose }: { iid: number | null; onClose: () => void }) {
  const diagnose = useDiagnose()
  const save = useSaveDraft()
  const failed = diagnose.error as ApiError | null
  const result = diagnose.data

  return (
    <Modal
      open={iid !== null}
      title={iid !== null ? `实例 ${iid} 的 AI 诊断` : ''}
      subtitle="先由规则层读串口日志模式计数；只有它给不出建议时才会调用模型，模型仅作归因与建议"
      width="max-w-2xl"
      onClose={() => {
        diagnose.reset()
        save.reset()
        onClose()
      }}
      footer={
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={() => {
              diagnose.reset()
              save.reset()
              onClose()
            }}
          >
            关闭
          </Button>
          {iid !== null && (
            <Button
              variant="primary"
              disabled={diagnose.isPending}
              icon={<BrainCircuit className="h-3 w-3" aria-hidden="true" />}
              onClick={() => diagnose.mutate(iid)}
            >
              {diagnose.isPending ? '诊断中' : diagnose.data ? '重新诊断' : '开始诊断'}
            </Button>
          )}
        </div>
      }
    >
      <div className="flex flex-col gap-3 text-2xs">
        {failed && (
          <div className="flex flex-col gap-1.5 rounded-card border border-danger/35 bg-danger/8 p-2.5">
            <p className="font-medium text-danger">诊断请求失败（HTTP {failed.status}）</p>
            <p className="leading-relaxed text-ink-300">{failed.detail}</p>
          </div>
        )}

        {!result && !failed && (
          <p className="leading-relaxed text-ink-700">
            诊断不会改变实例状态：它只读串口日志、最近几次运行与 rootfs 结构，产出一份归因与建议。
            模型不可用时诊断仍会返回，只是止于规则层的判断
          </p>
        )}

        {result && (
          <>
            <div className="flex flex-wrap items-center gap-1.5">
              {result.rule_recommendation !== null ? (
                <Badge tone="success">规则层已给出建议</Badge>
              ) : result.llm_used ? (
                <Badge tone="iris">模型已介入</Badge>
              ) : result.error !== null ? (
                <Badge tone="danger">模型调用失败</Badge>
              ) : (
                <Badge tone="neutral">未产生诊断</Badge>
              )}
              {result.decision && (
                <Badge tone="violet">置信度 {result.decision.confidence.toFixed(2)}</Badge>
              )}
            </div>

            {result.rule_recommendation !== null && (
              <div className="rounded-card border border-success/30 bg-success/8 p-2.5">
                <p className="font-medium text-success">确定性规则已处理，未调用模型</p>
                <p className="mt-1 font-mono leading-relaxed text-ink-200">
                  {result.rule_recommendation}
                </p>
              </div>
            )}

            {(result.disabled_reason !== null || result.error !== null) && (
              <div className="rounded-card border border-warning/30 bg-warning/8 p-2.5">
                <p className="font-medium text-warning">
                  {result.error !== null ? '模型调用失败，已降级为仅采集' : 'LLM 层未启用'}
                </p>
                <p className="mt-1 leading-relaxed text-ink-300">
                  {result.error ?? result.disabled_reason}
                </p>
              </div>
            )}

            {result.decision && (
              <>
                <div>
                  <p className="font-medium text-ink-300">模型归因</p>
                  <p className="mt-1 leading-relaxed text-ink-500">{result.decision.diagnosis}</p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-ink-700">建议动作</span>
                  <Badge tone={ACTION_TONE[result.decision.action]}>{ACTION_LABEL[result.decision.action]}</Badge>
                </div>
                {result.decision.verify_plan.length > 0 && (
                  <div>
                    <p className="font-medium text-ink-300">建议随后核查</p>
                    <ul className="mt-1 flex flex-col gap-1">
                      {result.decision.verify_plan.map((step) => (
                        <li key={step} className="flex items-start gap-1.5 leading-relaxed text-ink-500">
                          <ArrowRight className="mt-px h-3 w-3 shrink-0 text-ink-700" aria-hidden="true" />
                          {step}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {result.decision.plugin_draft !== null && (
                  <DraftProposal
                    iid={iid as number}
                    draft={result.decision.plugin_draft}
                    diagnosis={result.decision.diagnosis}
                    confidence={result.decision.confidence}
                    onSaved={onClose}
                  />
                )}
              </>
            )}

            <p className="border-t border-surface-border pt-2 leading-relaxed text-ink-700">
              {result.note}
            </p>
          </>
        )}

        {save.isError && (
          <p className="leading-relaxed text-danger">
            保存草案失败：{((save.error as ApiError).detail ?? (save.error as Error).message)}
          </p>
        )}
      </div>
    </Modal>
  )
}

const ACTION_LABEL: Record<LlmAction, string> = {
  NONE: '无需动作',
  WEB_SERVER_RESTART: '重启容器（可触达运行中 guest 的唯一修复）',
  DRAFT_PLUGIN: '编写规则插件（下次运行生效）',
}

const ACTION_TONE: Record<LlmAction, Tone> = {
  NONE: 'neutral',
  WEB_SERVER_RESTART: 'warning',
  DRAFT_PLUGIN: 'iris',
}

/** The draft a model proposed, shown before anything is written.
 *
 *  Saving is the only action here, and it writes to the draft directory -- which the
 *  engine does not load from. That is why this is a button rather than an automatic
 *  consequence of the diagnosis: nothing the model proposed can reach the next run
 *  until it is read on the plugin page and accepted there. */
function DraftProposal({
  iid,
  draft,
  diagnosis,
  confidence,
  onSaved,
}: {
  iid: number
  draft: PluginDraft
  diagnosis: string
  confidence: number
  onSaved: () => void
}) {
  const save = useSaveDraft()

  return (
    <div className="flex flex-col gap-2 rounded-card border border-iris-400/30 bg-iris-500/8 p-2.5">
      <p className="font-medium text-iris-400">模型建议的规则文档</p>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-card border border-surface-border bg-surface-code p-2 font-mono text-[11px] leading-relaxed text-ink-200">
        {draft.yaml}
      </pre>
      <div className="flex items-center gap-2">
        <p className="mr-auto leading-relaxed text-ink-700">
          保存到 ai-drafts 目录，不进引擎加载路径；需到插件页「AI 规则草案」审核后才会生效
        </p>
        <Button
          size="sm"
          disabled={save.isPending}
          icon={<FileDown className="h-3 w-3" aria-hidden="true" />}
          onClick={() =>
            save.mutate(
              {
                rule_id: draft.rule_id,
                stage: draft.stage,
                description: draft.description,
                yaml: draft.yaml,
                iid,
                confidence,
                diagnosis,
              },
              { onSuccess: onSaved },
            )
          }
        >
          {save.isPending ? '保存中' : '保存为草案'}
        </Button>
      </div>
      {save.isSuccess && <p className="leading-relaxed text-success">已保存为草案 {save.data.saved}</p>}
    </div>
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
  const [inspecting, setInspecting] = useState<number | null>(null)
  const [pendingErase, setPendingErase] = useState<{ kind: 'one'; run: RunItem } | { kind: 'all' } | null>(null)
  const erase = useEraseHistory(result)

  return (
    <>
      <Panel
        title="历史运行记录"
        subtitle="全库累计；筛选在服务端执行，分页计数与筛选一致"
        actions={
          <>
            <ExportButton />
            <Button
              size="sm"
              variant="danger"
              disabled={total === 0 || erase.isPending}
              onClick={() => setPendingErase({ kind: 'all' })}
              title="删除全部已记录的仿真运行"
            >
              <Eraser className="h-3.5 w-3.5" aria-hidden="true" />
              清空全部
            </Button>
          </>
        }
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
        <Select
          label="架构"
          value={arch}
          onChange={onArch}
          options={[
            { value: '', label: '全部' },
            ...['mipsel', 'mipseb', 'armel', 'arm64'].map((value) => ({ value, label: value })),
          ]}
        />
        <Select
          label="失败类型"
          value={kind}
          onChange={onKind}
          // The code travels with the name: `link-no-arp` is what the API filters on
          // and what the detail page's copy says, and a filter that hides it leaves
          // the reader to guess which of four similar Chinese names they picked.
          options={[
            { value: '', label: '全部' },
            ...kinds.map((value) => ({ value, label: failureLabel(value), hint: value })),
          ]}
        />
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
              <th className="px-3 py-2 font-medium">状态</th>
              <th className="px-3 py-2 font-medium">架构</th>
              <th className="px-3 py-2 font-medium">Web</th>
              <th className="px-3 py-2 font-medium">耗时</th>
              <th className="px-3 py-2 font-medium">结论</th>
              <th className="px-3 py-2 font-medium">开始</th>
              <th className="px-3 py-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {result.data.items.map((row) => (
              <tr
                key={row.id}
                onClick={() => setInspecting(row.id)}
                className="cursor-pointer border-b border-surface-border/60 last:border-0 hover:bg-surface-faint"
              >
                <td className="px-3 py-2">
                  <span className="font-mono text-xs text-iris-400">#{row.id}</span>
                </td>
                <td className="px-3 py-2 font-mono text-ink-300">{row.iid}</td>
                <td className="px-3 py-2">
                  {/* Which the instance stands in *now*, per the server's own
                      three-state verdict: the active table, a left-behind scratch
                      artefact, or neither. The words and the tone come from one
                      mapping, shared with the record window. */}
                  <Badge tone={runStateTone(row.state)}>{runStateLabel(row.state)}</Badge>
                </td>                <td className="px-3 py-2 font-mono text-ink-300">{row.arch || DASH}</td>
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
                {/* Stops the row click: deleting is not inspecting, and one of the
                    two opening the other's window is the kind of thing that only
                    shows up when somebody is in a hurry. */}
                <td className="px-3 py-2" onClick={(event) => event.stopPropagation()}>
                  <div className="flex items-center justify-end gap-1.5">
                    <Link to={`/instances/${row.iid}?run=${row.id}`}>
                      <Button size="sm" variant="ghost" title="打开实例详情">
                        实例页
                      </Button>
                    </Link>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => setPendingErase({ kind: 'one', run: row })}
                      title={`删除记录 #${row.id}`}
                      aria-label={`删除记录 ${row.id}`}
                    >
                      <Trash2 className="h-3 w-3" aria-hidden="true" />
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="border-t border-surface-border px-3 py-2 text-[10px] text-ink-700">
        上表只显示当前筛选下的一页；点击任意一行可查看该次运行的完整信息：统计卡与评测集使用全库口径，
        两处数字来自同一份聚合，不随分页或筛选变化
      </p>
      </Panel>

      <RunRecord runId={inspecting} onClose={() => setInspecting(null)} />

      <EraseConfirm
        pending={pendingErase}
        total={total}
        busy={erase.isPending}
        onCancel={() => setPendingErase(null)}
        onConfirm={() => {
          if (!pendingErase) return
          erase.mutate(pendingErase.kind === 'one' ? { id: pendingErase.run.id } : {})
          setPendingErase(null)
        }}
      />
      {erase.error && (
        <p className="text-2xs text-danger" role="alert">
          删除失败：{(erase.error as Error).message}
        </p>
      )}
    </>
  )
}

/** The delete, with both questions answered before it happens.
 *
 *  One confirmation rather than two shapes: what is about to go, and what survives.
 *  The bulk one therefore says that the firmware corpus is untouched *and* that the
 *  dashboard's cumulative rates will move -- those numbers are computed from exactly
 *  these rows, so a reader who does not expect the reach rate to change will read the
 *  change as a bug. */
function EraseConfirm({
  pending,
  total,
  busy,
  onCancel,
  onConfirm,
}: {
  pending: { kind: 'one'; run: RunItem } | { kind: 'all' } | null
  total: number
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  if (!pending) return null
  const one = pending.kind === 'one'
  return (
    <Modal
      open
      width="max-w-md"
      title={one ? `删除记录 #${pending.run.id}` : '清空全部历史记录'}
      subtitle={
        one
          ? `实例 ${pending.run.iid} · ${pending.run.arch || '未知架构'} · ${shortDateTime(pending.run.started_at)}`
          : `当前筛选下共 ${total} 条记录`
      }
      onClose={onCancel}
      footer={
        <>
          <span className="flex items-center gap-1.5 text-[10px] text-ink-700">
            <AlertTriangle className="h-3 w-3 text-warning" aria-hidden="true" />
            不可撤销
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Button variant="ghost" onClick={onCancel} disabled={busy}>
              取消
            </Button>
            <Button variant="danger" onClick={onConfirm} disabled={busy}>
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              {busy ? '删除中…' : one ? '删除这一条' : '删除全部'}
            </Button>
          </div>
        </>
      }
    >
      <div className="flex flex-col gap-2 text-2xs leading-relaxed text-ink-300">
        <p>
          这条记录下的失败画像与修复账本条目会一并删除，它们在数据库里声明了级联删除，但 SQLite 默认不
          启用外键约束，所以由服务端显式先删子表 —— 否则残留行会继续计入总览的失败统计，而那条运行早已不存在
        </p>
        <p>已登记的固件语料不受影响，评测集仍可从语料重新运行得到</p>
        {!one && (
          <p className="text-warning">
            注意：总览页与评测集的可达率是全库累计口径，删除后这些数字会随之变化
          </p>
        )}
      </div>
    </Modal>
  )
}

/** One mutation for both shapes, so the invalidation cannot drift between them.
 *
 *  Every cache that shows a run has to be invalidated, not just the paged list: the
 *  header and the footer quote `stats.total`, the evaluation set counts the same
 *  rows, and the plugin page tallies repairs out of the ledger. Leaving any one of
 *  them cached is how a screen ends up reporting a record that no longer exists. */
function useEraseHistory(result: UseQueryResult<RunsPage, Error>) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (target: { id?: number }) =>
      target.id === undefined ? api.clearRuns() : api.deleteRun(target.id),
    onSuccess: async () => {
      for (const key of [['runs'], ['stats'], ['eval-set'], ['root-causes'], ['rules'], ['run']]) {
        await client.invalidateQueries({ queryKey: key })
      }
      // A page whose only rows were just deleted is now past the end; without this
      // the table shows an empty page still labelled "第 3 / 3 页".
      await result.refetch()
    },
  })
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

