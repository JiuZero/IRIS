import { useState } from 'react'
import type { ReactNode } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  Boxes,
  Check,
  Clock,
  FileWarning,
  PackageMinus,
  ShieldCheck,
  Upload,
  X,
} from 'lucide-react'

import { ApiError } from '../lib/api'
import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  FileInput,
  Panel,
  Skeleton,
  StatusDot,
} from '../components/ui'
import type { Tone } from '../components/ui'
import { Modal } from '../components/Modal'
import { DASH } from '../lib/format'
import {
  useAiDrafts,
  useAiStatus,
  useInstallDraft,
  useInstallPlugin,
  useRejectDraft,
  useRemovePlugin,
  useRules,
} from '../hooks/queries'
import type { AiStatus, PluginDraftView, RulePlugin } from '../lib/types'

/**
 * The rule plugin library: what is actually in the box, and what can be added to it.
 *
 * Every row is a YAML document read through the engine's own loader, across both
 * directories it reads -- `rules/` and the plugin directory under `iris_home` -- so a
 * rule added to the repository appears here without a second inventory to keep in
 * step, and a row cannot claim to be a plugin that is not there. `origin` separates
 * the two because only an external one can be uninstalled, and a list that presented
 * them as the same kind of thing would be inviting an edit that cannot exist.
 *
 * The tallies come from the repair ledger, and they are labelled `applied`/`promoted`
 * rather than "命中次数" because that ledger records repairs *attempted and written
 * down against* a rule id, not the engine's per-run match report, which is not kept.
 * A page that called those hits would be quoting a number that means something else.
 *
 * **Uploading installs, it does not preview.** The server writes the document, re-reads
 * it through the loader, and refuses it unless the loader has nothing to complain
 * about -- so a rejection here is the loader's own objection, quoted in full, and
 * nothing is left on disk. The alternative, accepting a document whose unknown keys
 * the engine would silently ignore, is the failure this feature exists to remove.
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
  const external = items.filter((item) => item.origin === 'external')
  const [detail, setDetail] = useState<RulePlugin | null>(null)
  const [uploading, setUploading] = useState(false)

  return (
    <div className="flex flex-col gap-4 p-4">
      <Panel
        title="插件中心"
        subtitle="IRIS 自带的规则插件库，加上上传安装的外部插件；状态与账本数字由后端从规则目录与 repair_action 表读出"
        actions={
          <span className="flex items-center gap-2">
            {rules.isFetching && !rules.isError && (
              <span className="flex items-center gap-1 text-2xs text-ink-700">
                <StatusDot tone="iris" pulse />
                刷新
              </span>
            )}
            <Badge tone="neutral">{rules.data ? `${items.length} 个插件` : DASH}</Badge>
            <Button
              size="sm"
              icon={<Upload className="h-3 w-3" aria-hidden="true" />}
              onClick={() => setUploading(true)}
            >
              上传插件
            </Button>
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
              {external.length > 0 && <Badge tone="violet">外部插件 {external.length} 个</Badge>}
              <Badge tone={applied ? 'success' : 'neutral'}>已应用修复 {applied} 次</Badge>
              {warned.length > 0 && (
                <Badge tone="warning" icon={<AlertTriangle className="h-3 w-3" aria-hidden="true" />}>
                  {warned.length} 个带加载告警
                </Badge>
              )}
            </div>
            <p className="text-2xs leading-relaxed text-ink-500">
              上传的 YAML
              <span className="mx-1 rounded bg-surface-code px-1 font-mono text-[10px]">.yaml</span>
              会先被规则引擎完整校验再落盘：只要有一个键引擎不认识，或加载时产生任何告警，就拒绝安装并把原因原样退回，磁盘上不留文件
              ；格式与可用键见仓库
              <code className="mx-1 rounded bg-surface-code px-1 font-mono text-[10px]">docs/09-规则插件开发指南.md</code>
            </p>
            {items.length === 0 && (
              <EmptyState
                title="未读到任何规则文档"
                detail={`后端读取的目录是 ${rules.data?.dirs.join(' 与 ') || '—'}，这些目录不可读时页面如实显示为空，而不是假装有一份默认规则库`}
              />
            )}
          </div>
        )}
      </Panel>

      <AiDraftPanel />

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
            {detail?.origin === 'external' && (
              <UninstallButton plugin={detail} onDone={() => setDetail(null)} />
            )}
          </div>
        }
      >
        {detail && <PluginDetail plugin={detail} />}
      </Modal>

      <UploadPluginModal open={uploading} onClose={() => setUploading(false)} />

    </div>
  )
}

/** Uninstall lives in the detail window rather than on the card.
 *
 *  A card here is a `<button>`, and a button cannot contain a button -- the browser
 *  silently drops the inner one, which would leave a control that renders and does
 *  nothing. Putting it in the window also puts a destructive action behind the one
 *  place the operator has just read what the rule actually does, and behind an
 *  explicit label rather than a bare trash icon. */
function UninstallButton({ plugin, onDone }: { plugin: RulePlugin; onDone: () => void }) {
  const remove = useRemovePlugin()
  const failed = remove.error as ApiError | null

  if (failed) {
    return (
      <p className="mr-auto max-w-xs truncate text-2xs text-danger" title={failed.detail}>
        {failed.detail}
      </p>
    )
  }
  return (
    <Button
      variant="danger"
      disabled={remove.isPending}
      icon={<PackageMinus className="h-3 w-3" aria-hidden="true" />}
      onClick={() => remove.mutate(plugin.source_file, { onSuccess: onDone })}
    >
      {remove.isPending ? '卸载中' : `卸载 ${plugin.source_file}`}
    </Button>
  )
}

/** The install window: pick a document, send it, and report exactly what the loader
 *  said about it.
 *
 *  A 422's `detail` is shown verbatim rather than replaced with a fixed sentence.
 *  The server answers with the loader's own objection -- which key it did not
 *  recognise, where in the document it was, what the text next to it was -- and
 *  replacing that with "上传失败" would throw away the only part the author of the
 *  document can act on. */
function UploadPluginModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const install = useInstallPlugin()
  const [file, setFile] = useState<File | null>(null)
  const failed = install.error as ApiError | null

  return (
    <Modal
      open={open}
      title="上传规则插件"
      subtitle="上传的文档会先被引擎校验，通过后存入插件目录并立即参与规则匹配"
      width="max-w-xl"
      onClose={() => {
        setFile(null)
        install.reset()
        onClose()
      }}
      footer={
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="ghost"
            onClick={() => {
              setFile(null)
              install.reset()
              onClose()
            }}
          >
            取消
          </Button>
          <Button
            variant="primary"
            disabled={!file || install.isPending}
            onClick={() => {
              if (file) install.mutate(file, { onSuccess: () => setFile(null) })
            }}
          >
            {install.isPending ? '校验并安装中' : '安装'}
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-3 text-2xs">
        <FileInput
          label="规则文档"
          hint="只接受 .yaml 与 .yml，单个文件不超过 1 MiB；上传后按文档里的 id 存为 <id>.yaml"
          file={file}
          accept=".yaml,.yml"
          disabled={install.isPending}
          onPick={setFile}
        />
        {failed && (
          <div className="flex flex-col gap-1.5 rounded-card border border-danger/35 bg-danger/8 p-2.5">
            <p className="font-medium text-danger">
              {failed.status === 422 ? '引擎拒绝了这份文档' : `安装失败（HTTP ${failed.status}）`}
            </p>
            <p className="leading-relaxed text-ink-300">{failed.detail}</p>
          </div>
        )}
        {install.isSuccess && !failed && (
          <p className="leading-relaxed text-success">
            已安装 {install.data.id}，存为 {install.data.source_file}，规则列表已刷新
          </p>
        )}
        <p className="leading-relaxed text-ink-700">
          上传不会覆盖同名规则：id 与既有规则重复时安装被拒。内置规则不在插件目录里，无法通过卸载按钮移除
        </p>
      </div>
    </Modal>
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
            <p className="mt-0.5 text-[10px] text-ink-500">
              目标阶段：{plugin.stage}
              {/* The origin sits next to the id rather than in the bottom strip: it
                  decides what the reader may do next (uninstall, or not), and it is
                  the one field whose absence would make a card's affordances a
                  guess. */}
              <span className={plugin.origin === 'external' ? 'text-violet' : 'text-ink-700'}>
                {' · '}
                {plugin.origin === 'external' ? '外部安装' : 'IRIS 内置'}
              </span>
            </p>
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
          {/* Direction only, no label. "完整信息 →" spelled out an affordance the
              whole card already is: the card is the button, so a corner announcing
              it is a label for the label. The arrow keeps the card readable as
              "there is more past this edge" for anyone who does not take
              `cursor: pointer` as an invitation, and the card's `aria-label`
              carries the same fact to anyone not looking at it. */}
          <ArrowRight className="ml-auto h-3 w-3 shrink-0 text-ink-700" aria-hidden="true" />
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

/** What the card left out, in one window: where the document came from, the whole
 *  description, every declared condition, action and post-fix check, the warnings with
 *  their text, and the bookkeeping note that says what the counts do and do not
 *  mean. */
function PluginDetail({ plugin }: { plugin: RulePlugin }) {
  return (
    <div className="flex flex-col gap-3 text-2xs">
      <DetailRow title="来源">
        <Badge tone={plugin.origin === 'external' ? 'violet' : 'neutral'}>
          {plugin.origin === 'external' ? '外部安装的插件' : 'IRIS 内置规则'}
        </Badge>
        <span className="font-mono text-ink-300">{plugin.source_file}</span>
        <span className="text-ink-700">
          {plugin.origin === 'external' ? '可在下方卸载' : '内置规则不可卸载'}
        </span>
      </DetailRow>

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
/** The rule drafts a model has proposed, waiting for a person to accept or refuse.
 *
 *  **This panel is the review, and the review is the safety property.** A draft lives
 *  in `iris_home/ai-drafts`, which the engine never loads from: nothing here reaches
 *  the next run until someone presses 接受, and that press goes through the same
 *  validation chain as the upload above. So the panel's job is not to be reassuring
 *  about the model's output -- it is to show enough of it that a person can refuse it.
 *  Hence the full YAML text, the originating instance, and the model's confidence
 *  shown as the model's own claim rather than as a score to gate on.
 *
 *  A rejected draft stays listed as `rejected` rather than disappearing. "There is
 *  nothing here" and "someone looked at this and said no" are different facts about
 *  the same run, and the second one is what makes the loop auditable. */
function AiDraftPanel() {
  const status = useAiStatus()
  const drafts = useAiDrafts()
  const [detail, setDetail] = useState<PluginDraftView | null>(null)
  const pending = (drafts.data?.drafts ?? []).filter((draft) => draft.status === 'pending')

  return (
    <Panel
      title="AI 规则草案"
      subtitle="模型给出的规则文档，存放在 ai-drafts 目录，不在引擎加载路径上；接受时走与上传完全相同的校验链"
      actions={
        <span className="flex items-center gap-2">
          <Badge tone={status.data?.state === 'ready' ? 'iris' : 'neutral'}>
            {status.data ? AI_STATE_LABEL[status.data.state] : DASH}
          </Badge>
          <Badge tone={pending.length > 0 ? 'warning' : 'neutral'}>待审 {pending.length}</Badge>
        </span>
      }
    >
      {drafts.isLoading && <Skeleton className="h-16 w-full" />}
      {drafts.isError && (
        <ErrorState
          title="无法读取草案"
          detail={(drafts.error as Error).message}
          onRetry={() => void drafts.refetch()}
        />
      )}
      {status.data && <p className="text-2xs leading-relaxed text-ink-500">{status.data.note}</p>}
      {drafts.data && drafts.data.drafts.length === 0 && (
        <EmptyState
          title="暂无草案"
          detail="在实例页对某次运行做「AI 诊断」，模型建议写插件时再保存到这里；没有草案时引擎行为完全不变"
        />
      )}
      {drafts.data && drafts.data.drafts.length > 0 && (
        <div className="mt-3 flex flex-col gap-2">
          {drafts.data.drafts.map((draft) => (
            <DraftRow key={draft.rule_id} draft={draft} onOpen={() => setDetail(draft)} />
          ))}
        </div>
      )}
      <DraftDetailModal draft={detail} onClose={() => setDetail(null)} />
    </Panel>
  )
}

const AI_STATE_LABEL: Record<AiStatus['state'], string> = {
  ready: 'LLM 层就绪',
  disabled: 'LLM 层未配置',
  unreachable: 'LLM 层配置不完整',
}

function DraftRow({ draft, onOpen }: { draft: PluginDraftView; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`查看草案 ${draft.rule_id} 的完整文档`}
      className="glass lift block rounded-card p-0 text-left transition-colors hover:border-iris-400/50"
    >
      <div className="flex flex-col gap-2 p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate font-mono text-xs font-medium text-ink-100">{draft.rule_id}</p>
            <p className="mt-0.5 text-[10px] text-ink-500">
              目标阶段：{draft.stage}
              <span className="text-ink-700">
                {' · '}
                {draft.source_iid !== null ? `来自实例 ${draft.source_iid}` : '手工保存，无运行来源'}
              </span>
            </p>
          </div>
          <span className="flex shrink-0 items-center gap-1.5">
            <Badge tone={DRAFT_TONE[draft.status]}>{DRAFT_LABEL[draft.status]}</Badge>
            <Badge tone="iris">置信度 {draft.confidence.toFixed(2)}</Badge>
          </span>
        </div>
        <p className="line-clamp-2 text-2xs leading-relaxed text-ink-300" title={draft.diagnosis}>
          {draft.diagnosis || draft.description}
        </p>
        <div className="flex items-center gap-1 border-t border-surface-border pt-2 text-[10px] text-ink-700">
          <Clock className="h-3 w-3 shrink-0" aria-hidden="true" />
          {draft.created_at}
          <ArrowRight className="ml-auto h-3 w-3 shrink-0" aria-hidden="true" />
        </div>
      </div>
    </button>
  )
}

const DRAFT_TONE: Record<PluginDraftView['status'], Tone> = {
  pending: 'warning',
  accepted: 'success',
  rejected: 'neutral',
}

const DRAFT_LABEL: Record<PluginDraftView['status'], string> = {
  pending: '待审',
  accepted: '已接受',
  rejected: '已拒绝',
}

/** The review window: the whole document, its provenance, and the two buttons.
 *
 *  The YAML is shown in full and unedited because this is the text that will be
 *  handed to the engine, and a review that reads a summary of it is a rubber stamp.
 *  The install path refuses anything the engine would refuse from a person, and says
 *  why in the loader's own words -- so a 422 here is information, not a failed action. */
function DraftDetailModal({
  draft,
  onClose,
}: {
  draft: PluginDraftView | null
  onClose: () => void
}) {
  const install = useInstallDraft()
  const reject = useRejectDraft()
  const failed = (install.error ?? reject.error) as ApiError | null

  return (
    <Modal
      open={draft !== null}
      title={draft?.rule_id ?? ''}
      subtitle={
        draft
          ? `目标阶段：${draft.stage} · ${DRAFT_LABEL[draft.status]} · 模型置信度 ${draft.confidence.toFixed(2)}`
          : ''
      }
      width="max-w-3xl"
      onClose={() => {
        install.reset()
        reject.reset()
        onClose()
      }}
      footer={
        <div className="ml-auto flex items-center gap-2">
          {failed && (
            <p className="mr-auto max-w-sm text-2xs leading-relaxed text-danger">{failed.detail}</p>
          )}
          <Button
            variant="ghost"
            onClick={() => {
              install.reset()
              reject.reset()
              onClose()
            }}
          >
            关闭
          </Button>
          {draft?.status === 'pending' && (
            <>
              <Button
                variant="danger"
                disabled={reject.isPending}
                icon={<X className="h-3 w-3" aria-hidden="true" />}
                onClick={() => reject.mutate(draft.rule_id, { onSuccess: onClose })}
              >
                {reject.isPending ? '拒绝中' : '拒绝'}
              </Button>
              <Button
                variant="primary"
                disabled={install.isPending}
                icon={<Check className="h-3 w-3" aria-hidden="true" />}
                onClick={() => install.mutate(draft.rule_id, { onSuccess: onClose })}
              >
                {install.isPending ? '校验并安装中' : '接受并安装'}
              </Button>
            </>
          )}
        </div>
      }
    >
      {draft && (
        <div className="flex flex-col gap-3 text-2xs">
          <DetailRow title="来源">
            <Badge tone="iris">
              {draft.source_iid !== null ? `实例 ${draft.source_iid} 的诊断` : '手工保存'}
            </Badge>
            <span className="text-ink-700">{draft.created_at}</span>
          </DetailRow>
          <div>
            <p className="font-medium text-ink-300">模型给出的归因</p>
            <p className="mt-1 leading-relaxed text-ink-500">{draft.diagnosis || '（该草案未附归因说明）'}</p>
          </div>
          <div>
            <p className="font-medium text-ink-300">规则文档原文</p>
            <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap rounded-card border border-surface-border bg-surface-code p-2.5 font-mono text-[11px] leading-relaxed text-ink-200">
              {draft.yaml}
            </pre>
          </div>
          <p className="leading-relaxed text-ink-700">
            接受后文档存为
            <code className="mx-1 rounded bg-surface-code px-1 font-mono text-[10px]">
              {draft.rule_id}.yaml
            </code>
            进入插件目录，下次运行生效；同时在 repair_action 记一条
            <code className="mx-1 rounded bg-surface-code px-1 font-mono text-[10px]">source=llm</code>
            且 promoted 的条目，因此同类故障下次由规则层零成本处理
          </p>
        </div>
      )}
    </Modal>
  )
}
