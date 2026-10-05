import { Link } from 'react-router-dom'
import { ExternalLink, Info } from 'lucide-react'

import { Modal } from './Modal'
import { Badge, DataRow, ErrorState, Skeleton, StatusDot } from './ui'
import { DASH, failureLabel, layerLabel, seconds, shortDateTime } from '../lib/format'
import { useRun } from '../hooks/queries'

/**
 * One recorded run, in full.
 *
 * The history table answers "what happened" in five columns. This window answers
 * "why", and it is a window rather than a page because the question is asked *about
 * a row* -- the answer is worthless anywhere else, and navigating away from the table
 * to read it means coming back and losing your place.
 *
 * Everything shown here is what the run recorded at the time. The four-layer profile
 * in particular is rebuilt from the stored probe evidence rather than re-measured:
 * a guest that booted ten minutes ago cannot be probed again, and showing a fresh
 * reading under a historical verdict would compare two different things.
 */
export function RunRecord({ runId, onClose }: { runId: number | null; onClose: () => void }) {
  const query = useRun(runId)

  return (
    <Modal
      open={runId !== null}
      width="max-w-3xl"
      title={runId === null ? '运行详情' : `运行记录 #${runId}`}
      subtitle="该次运行当时记录的全部信息；链路结论来自随运行落盘的探测证据，不重新测量"
      onClose={onClose}
    >
      {query.isLoading && <Skeleton className="h-48 w-full" />}
      {query.isError && (
        <ErrorState
          title="无法读取该次运行"
          detail={(query.error as Error).message}
          onRetry={() => void query.refetch()}
        />
      )}
      {query.data && <RecordBody run={query.data} />}
    </Modal>
  )
}

function RecordBody({ run }: { run: NonNullable<ReturnType<typeof useRun>['data']> }) {
  return (
    <div className="flex flex-col gap-5">
      <section>
        <SectionTitle>结论</SectionTitle>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={run.web_ok === null ? 'neutral' : run.web_ok ? 'success' : 'danger'}>
            {run.web_ok === null ? 'Web 未探测' : run.web_ok ? 'Web 可达' : 'Web 不可达'}
          </Badge>
          {run.result_kind ? (
            <Badge tone={run.result_kind.startsWith('link-no') ? 'warning' : 'danger'}>
              {failureLabel(run.result_kind)}
            </Badge>
          ) : (
            <Badge tone="success">仿真成功</Badge>
          )}
          <Badge tone="neutral">{run.arch || '架构未记录'}</Badge>
          <Link
            to={`/instances/${run.iid}`}
            className="ml-auto flex items-center gap-1 text-2xs text-iris-400 hover:underline"
          >
            <ExternalLink className="h-3 w-3" aria-hidden="true" />
            打开实例 {run.iid} 详情
          </Link>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
        <SectionTitle>运行参数</SectionTitle>
        <div className="col-span-full">
          <DataRow label="实例 iid" mono>
            {run.iid}
          </DataRow>
          <DataRow label="架构" mono>
            {run.arch || DASH}
          </DataRow>
          <DataRow label="guest IP" mono>
            {run.ip || DASH}
          </DataRow>
          <DataRow label="HTTP 探通耗时" mono>
            {seconds(run.time_web)}
          </DataRow>
          <DataRow label="ICMP 探通耗时" mono>
            {seconds(run.time_ping)}
          </DataRow>
          <DataRow label="开始" mono>
            {shortDateTime(run.started_at)}
          </DataRow>
          <DataRow label="结束" mono>
            {shortDateTime(run.finished_at)}
          </DataRow>
        </div>
      </section>

      <section>
        <SectionTitle>四层链路</SectionTitle>
        {run.link === null ? (
          <p className="text-2xs leading-relaxed text-ink-500">
            这次运行没有留下链路证据。干净的运行不做探测，无法探测的运行也就没有证据可留 —— 两者都
            显示为空，而不是补一个「全部通过」。
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="neutral" icon={<Info className="h-3 w-3" aria-hidden="true" />}>
                guest IP {run.link.guest_ip || DASH}
              </Badge>
              <Badge tone="warning">首个不通：{run.link.first_break ? layerLabel(run.link.first_break) : '无'}</Badge>
              {run.link.unavailable && <Badge tone="neutral">不可用层：{run.link.unavailable}</Badge>}
            </div>
            <ul className="mt-2 flex flex-col gap-1">
              {run.link.layers.map((item) => (
                <li
                  key={item.layer}
                  className="flex items-start gap-2 rounded-card border border-surface-border px-2.5 py-1.5"
                >
                  <StatusDot
                    tone={item.state === 'ok' ? 'success' : item.state === 'blocked' ? 'danger' : 'warning'}
                  />
                  <span className="w-12 shrink-0 font-mono text-2xs text-ink-100">
                    {layerLabel(item.layer)}
                  </span>
                  <Badge tone={item.state === 'ok' ? 'success' : item.state === 'blocked' ? 'danger' : 'warning'}>
                    {item.state === 'ok' ? '通' : item.state === 'blocked' ? '不通' : '未判定'}
                  </Badge>
                  <span className="min-w-0 flex-1 text-2xs leading-relaxed text-ink-500">{item.detail}</span>
                </li>
              ))}
            </ul>
            {run.link.note && (
              <p className="mt-2 text-[10px] leading-relaxed text-ink-700">{run.link.note}</p>
            )}
          </>
        )}
      </section>

      <section>
        <SectionTitle>失败画像（{run.failures.length}）</SectionTitle>
        {run.failures.length === 0 ? (
          <p className="text-2xs leading-relaxed text-ink-500">
            没有记录到失败信号。一次失败的运行通常有多个叠加原因，每个信号各占一行，这里全空说明确实没有。
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {run.failures.map((item, index) => (
              <li key={`${item.stage}-${item.signal}-${index}`} className="rounded-card border border-surface-border px-2.5 py-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="danger">{item.stage || '未知阶段'}</Badge>
                  <span className="text-2xs text-ink-100">{failureLabel(item.signal)}</span>
                  {item.log_fingerprint && (
                    <span
                      className="ml-auto max-w-full truncate font-mono text-[10px] text-ink-700"
                      title={item.log_fingerprint}
                    >
                      {item.log_fingerprint}
                    </span>
                  )}
                </div>
                {Object.keys(item.detail ?? {}).length > 0 && (
                  <pre className="scroll-y mt-1 max-h-24 whitespace-pre-wrap break-all rounded bg-surface-code p-1.5 font-mono text-[10px] leading-relaxed text-ink-300">
                    {JSON.stringify(item.detail, null, 2)}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionTitle>修复账本（{run.repairs.length}）</SectionTitle>
        {run.repairs.length === 0 ? (
          <p className="text-2xs leading-relaxed text-ink-500">
            这次运行没有写入任何修复动作。没有修复不等于没有失败信号：规则未匹配与未执行修复是两种情况，
            上面的失败画像是前者唯一的证据。
          </p>
        ) : (
          <table className="w-full text-left text-2xs">
            <thead>
              <tr className="border-b border-surface-border text-ink-500">
                <th className="py-1.5 font-medium">来源</th>
                <th className="py-1.5 font-medium">规则</th>
                <th className="py-1.5 font-medium">证据</th>
                <th className="py-1.5 font-medium">已应用</th>
                <th className="py-1.5 font-medium">已沉淀</th>
              </tr>
            </thead>
            <tbody>
              {run.repairs.map((item, index) => (
                <tr key={`${item.rule_id}-${index}`} className="border-b border-surface-border/60 last:border-0 align-top">
                  <td className="py-1.5">
                    <Badge tone={item.source === 'rule' ? 'iris' : item.source === 'llm' ? 'violet' : 'neutral'}>
                      {sourceLabel(item.source)}
                    </Badge>
                  </td>
                  <td className="py-1.5 font-mono text-ink-300">{item.rule_id || DASH}</td>
                  <td className="max-w-xs py-1.5 leading-relaxed text-ink-500">{item.evidence || DASH}</td>
                  <td className="py-1.5">
                    <YesNo value={item.applied} />
                  </td>
                  <td className="py-1.5">
                    <YesNo value={item.promoted} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-700">{children}</h3>
  )
}

function YesNo({ value }: { value: boolean }) {
  return <span className={value ? 'text-success' : 'text-ink-700'}>{value ? '是' : '否'}</span>
}

/** `repair_action.source` is one of three declared values. An unknown one would be a
 *  server-side change, so it is printed rather than folded into "其他". */
function sourceLabel(source: string): string {
  return { rule: '规则', llm: '模型', manual: '人工' }[source] ?? source
}

