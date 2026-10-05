import { AlertTriangle, Boxes, FileWarning, ShieldCheck } from 'lucide-react'

import { Badge, EmptyState, ErrorState, Panel, Skeleton, StatusDot } from '../components/ui'
import { DASH } from '../lib/format'
import { useRules } from '../hooks/queries'
import type { RulePlugin } from '../lib/types'

/**
 * The rule plugin library: what is actually in the box.
 *
 * Every row is a YAML document read out of `rules/` through the engine's own
 * loader, so a rule added to the repository appears here without a second inventory
 * to keep in step -- and a row cannot claim to be a plugin that is not there. The
 * tallies come from the repair ledger, and they are labelled `applied`/`promoted`
 * rather than "命中次数" because that ledger records repairs *attempted and written
 * down against* a rule id, not the engine's per-run match report, which is not kept.
 * A page that called those hits would be quoting a number that means something else.
 *
 * There is no upload button, and the panel says so in words. A plugin centre that
 * accepts a file has to be able to run it, to sandbox it, and to say what it changed;
 * this one can do none of those, so offering the control would be a promise the
 * product cannot keep.
 */
export function Plugins() {
  const rules = useRules()
  const items = rules.data?.items ?? []
  const applied = items.reduce((total, item) => total + item.applied, 0)
  const warned = items.filter((item) => item.warnings.length > 0)

  return (
    <div className="flex flex-col gap-4 p-4">
      <Panel
        title="插件中心"
        subtitle="IRIS 自带的规则插件库；状态与账本数字由后端从 rules/ 目录与 repair_action 表读出"
        actions={
          <span className="flex items-center gap-2">
            {rules.isFetching && !rules.isError && (
              <span className="flex items-center gap-1 text-2xs text-ink-700">
                <StatusDot tone="iris" pulse />
                刷新
              </span>
            )}
            <Badge tone="neutral">{rules.data ? `${items.length} 个插件` : DASH}</Badge>
          </span>
        }
      >
        {rules.isLoading && <Skeleton className="h-24 w-full" />}
        {rules.isError && (
          <ErrorState
            title="无法读取规则插件"
            detail={(rules.error as Error).message}
            onRetry={() => void rules.refetch()}
          />
        )}
        {rules.data && (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="iris" icon={<Boxes className="h-3 w-3" aria-hidden="true" />}>
                规则 {items.length} 个
              </Badge>
              <Badge tone={applied ? 'success' : 'neutral'}>已应用修复 {applied} 次</Badge>
              {warned.length > 0 && (
                <Badge tone="warning" icon={<AlertTriangle className="h-3 w-3" aria-hidden="true" />}>
                  {warned.length} 个带加载告警
                </Badge>
              )}
            </div>
            <p className="text-2xs leading-relaxed text-ink-500">
              规则插件随 IRIS 自带，<span className="text-ink-300">当前不支持从外部安装或上传</span>：
              规则在仿真前被加载并逐条匹配，页面只能读取，不提供编辑入口。要增补规则，在
              <code className="mx-1 rounded bg-surface-code px-1 font-mono text-[10px]">rules/</code>
              目录增加 YAML 文档后重启服务，本页会自动出现该条目。
            </p>
            {items.length === 0 && (
              <EmptyState
                title="未读到任何规则文档"
                detail={`后端返回的目录是 ${rules.data?.source ?? '—'}。该目录不可读时页面如实显示为空，而不是假装有一份默认规则库`}
              />
            )}
          </div>
        )}
      </Panel>

      {items.length > 0 && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {items.map((item) => (
            <PluginCard key={item.id} plugin={item} />
          ))}
        </div>
      )}
    </div>
  )
}

/** One rule, with the three things that decide whether it is any use: what it
 *  matches on, what it does, and how often it has actually been applied. */
function PluginCard({ plugin }: { plugin: RulePlugin }) {
  return (
    <Panel
      title={plugin.id}
      subtitle={`目标阶段：${plugin.stage}`}
      actions={
        <span className="flex items-center gap-1.5">
          <Badge tone={plugin.applied ? 'success' : 'neutral'}>已应用 {plugin.applied}</Badge>
          <Badge tone={plugin.promoted ? 'violet' : 'neutral'}>已沉淀 {plugin.promoted}</Badge>
        </span>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-2xs leading-relaxed text-ink-300">{plugin.description}</p>

        <div>
          <p className="text-[10px] font-medium uppercase tracking-wider text-ink-700">匹配条件</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {plugin.detect.map((kind) => (
              <Badge key={kind} tone="cyan">
                {kind}
              </Badge>
            ))}
          </div>
        </div>

        <div>
          <p className="text-[10px] font-medium uppercase tracking-wider text-ink-700">修复动作</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {plugin.actions.length === 0 && <span className="text-2xs text-ink-700">未声明修复动作</span>}
            {plugin.actions.map((kind, index) => (
              <Badge key={`${kind}-${index}`} tone="iris">
                {kind}
              </Badge>
            ))}
          </div>
        </div>

        {plugin.verify.length > 0 && (
          <div>
            <p className="text-[10px] font-medium uppercase tracking-wider text-ink-700">修复后校验</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {plugin.verify.map((check) => (
                <Badge key={check} tone="neutral" icon={<ShieldCheck className="h-3 w-3" aria-hidden="true" />}>
                  {check}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {plugin.warnings.length > 0 && (
          <ul className="flex flex-col gap-1 border-t border-surface-border pt-2">
            {plugin.warnings.map((warning) => (
              <li key={warning} className="flex items-start gap-1.5 text-[10px] leading-relaxed text-warning">
                <FileWarning className="mt-px h-3 w-3 shrink-0" aria-hidden="true" />
                {warning}
              </li>
            ))}
          </ul>
        )}

        <p className="border-t border-surface-border pt-2 text-[10px] leading-relaxed text-ink-700">
          <span className="tnum text-ink-500">记账 {plugin.recorded} 条</span>
          ：数字来自修复账本中记在此规则 id 下的条目，含已应用与未应用的记录，不等于匹配命中次数
        </p>
      </div>
    </Panel>
  )
}

