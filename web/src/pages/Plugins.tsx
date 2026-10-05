import { useState } from 'react'
import type { ReactNode } from 'react'
import { AlertTriangle, ArrowRight, Boxes, FileWarning, ShieldCheck } from 'lucide-react'

import { Badge, Button, EmptyState, ErrorState, Panel, Skeleton, StatusDot } from '../components/ui'
import { Modal } from '../components/Modal'
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
 *
 * The card is a summary and the click is the rest of it. Descriptions here run to
 * three or four lines, and at two per row that is a ragged edge: one card two lines
 * taller than its neighbour, and a grid whose height is set by whichever description
 * happened to be longest. So the card clamps to two lines, keeps every card the same
 * height, and carries the full text in the title attribute; the window that opens on
 * click is where the description is read properly, next to the numbers and the
 * warnings that give it meaning.
 */
export function Plugins() {
  const rules = useRules()
  const items = rules.data?.items ?? []
  const applied = items.reduce((total, item) => total + item.applied, 0)
  const warned = items.filter((item) => item.warnings.length > 0)
  const [detail, setDetail] = useState<RulePlugin | null>(null)

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
              规则在仿真前被加载并逐条匹配，页面只能读取，不提供编辑入口；要在
              <code className="mx-1 rounded bg-surface-code px-1 font-mono text-[10px]">rules/</code>
              目录增加 YAML 文档后重启服务，本页会自动出现该条目
            </p>
            {items.length === 0 && (
              <EmptyState
                title="未读到任何规则文档"
                detail={`后端返回的目录是 ${rules.data?.source ?? '—'}，该目录不可读时页面如实显示为空，而不是假装有一份默认规则库`}
              />
            )}
          </div>
        )}
      </Panel>

      {items.length > 0 && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {items.map((item) => (
            <PluginCard key={item.id} plugin={item} onOpen={() => setDetail(item)} />
          ))}
        </div>
      )}

      <Modal
        open={detail !== null}
        title={detail?.id ?? ''}
        subtitle={detail ? `目标阶段：${detail.stage} · 已应用 ${detail.applied} 次 · 已沉淀 ${detail.promoted} 条` : ''}
        onClose={() => setDetail(null)}
        footer={
          <div className="ml-auto flex items-center gap-2">
            <Button variant="ghost" onClick={() => setDetail(null)}>
              关闭
            </Button>
          </div>
        }
      >
        {detail && <PluginDetail plugin={detail} />}
      </Modal>
    </div>
  )
}

/** One rule, with the three things that decide whether it is any use: what it
 *  matches on, what it does, and how often it has actually been applied.
 *
 *  The whole card is the button. A "details" affordance in the corner is a target
 *  the eye has to find, and on a card whose text is already two lines of preview it
 *  competes with the preview for attention; the card itself already reads as a unit,
 *  and `aria-label` says what activating it does. */
function PluginCard({ plugin, onOpen }: { plugin: RulePlugin; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`查看 ${plugin.id} 的完整信息`}
      className="glass lift block rounded-panel p-0 text-left transition-colors hover:border-iris-400/50"
    >
      <div className="flex flex-col gap-2.5 p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate font-mono text-xs font-medium text-ink-100">{plugin.id}</p>
            <p className="mt-0.5 text-[10px] text-ink-500">目标阶段：{plugin.stage}</p>
          </div>
          <span className="flex shrink-0 items-center gap-1.5">
            <Badge tone={plugin.applied ? 'success' : 'neutral'}>已应用 {plugin.applied}</Badge>
            <Badge tone={plugin.promoted ? 'violet' : 'neutral'}>已沉淀 {plugin.promoted}</Badge>
          </span>
        </div>

        {/* `line-clamp-2` rather than a JS truncation: CSS clamps to *rendered* lines,
            so the second line is full whatever the font size and density, and the
            ellipsis lands in the right place. A slice by character count would cut
            mid-word on a longer name and leave the clamp height to the text. */}
        <p className="line-clamp-2 text-2xs leading-relaxed text-ink-300" title={plugin.description}>
          {plugin.description}
        </p>

        <div className="flex flex-wrap items-center gap-1 border-t border-surface-border pt-2">
          {plugin.detect.map((kind) => (
            <Badge key={kind} tone="cyan">
              {kind}
            </Badge>
          ))}
          {plugin.actions.map((kind, index) => (
            <Badge key={`${kind}-${index}`} tone="iris">
              {kind}
            </Badge>
          ))}
          <span className="ml-auto flex items-center gap-1 text-ink-700">
            完整信息
            <ArrowRight className="h-3 w-3" aria-hidden="true" />
          </span>
        </div>

        {plugin.warnings.length > 0 && (
          <p className="flex items-center gap-1 text-[10px] text-warning">
            <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden="true" />
            {plugin.warnings.length} 条加载告警
          </p>
        )}
      </div>
    </button>
  )
}

/** What the card left out, in one window: the whole description, every declared
 *  condition, action and post-fix check, the warnings with their text, and the
 *  bookkeeping note that says what the counts do and do not mean. */
function PluginDetail({ plugin }: { plugin: RulePlugin }) {
  return (
    <div className="flex flex-col gap-3 text-2xs">
      <p className="leading-relaxed text-ink-300">{plugin.description}</p>

      <DetailRow title="匹配条件">
        {plugin.detect.map((kind) => (
          <Badge key={kind} tone="cyan">
            {kind}
          </Badge>
        ))}
      </DetailRow>

      <DetailRow title="修复动作">
        {plugin.actions.length === 0 && <span className="text-ink-700">未声明修复动作</span>}
        {plugin.actions.map((kind, index) => (
          <Badge key={`${kind}-${index}`} tone="iris">
            {kind}
          </Badge>
        ))}
      </DetailRow>

      {plugin.verify.length > 0 && (
        <DetailRow title="修复后校验">
          {plugin.verify.map((check) => (
            <Badge key={check} tone="neutral" icon={<ShieldCheck className="h-3 w-3" aria-hidden="true" />}>
              {check}
            </Badge>
          ))}
        </DetailRow>
      )}

      {plugin.warnings.length > 0 && (
        <div className="border-t border-surface-border pt-2">
          <p className="font-medium text-warning">加载告警 {plugin.warnings.length} 条</p>
          <ul className="mt-1 flex flex-col gap-1">
            {plugin.warnings.map((warning) => (
              <li key={warning} className="flex items-start gap-1.5 leading-relaxed text-warning">
                <FileWarning className="mt-px h-3 w-3 shrink-0" aria-hidden="true" />
                {warning}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="border-t border-surface-border pt-2 leading-relaxed text-ink-700">
        <span className="tnum text-ink-500">记账 {plugin.recorded} 条</span>
        ：数字来自修复账本中记在此规则 id 下的条目，含已应用与未应用的记录，不等于匹配命中次数
      </p>
    </div>
  )
}

function DetailRow({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <p className="font-medium text-ink-300">{title}</p>
      <div className="mt-1 flex flex-wrap gap-1">{children}</div>
    </div>
  )
}

