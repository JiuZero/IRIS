import { useEffect, useRef, useState } from 'react'
import { Check, CircleHelp, KeyRound, Moon, Palette, RefreshCw, Sun, X } from 'lucide-react'

import { Badge, Button, DataRow, ErrorState, Skeleton, TextInput } from './ui'
import { ApiError, api, readToken, writeToken } from '../lib/api'
import { classNames, DASH } from '../lib/format'
import { useConfig } from '../hooks/queries'
import {
  DENSITIES,
  DENSITY_LABELS,
  FONT_LABELS,
  THEMES,
  UI_FONTS,
  useAppearanceStore,
  type Density,
  type UiFont,
} from '../store/appearance'

/**
 * Settings, as a sheet over the workbench rather than a page of its own.
 *
 * The shape comes from the reference design: a panel docked above the bottom bar on
 * the right, opened from the one icon in the footer, dismissed by its own close
 * button or by clicking away. It is **not** a route. That is the whole point -- a
 * preference is a question asked *about* the page you are looking at, so answering it
 * should not cost you the page. Making it a route meant the answer was one click away
 * and the thing you were looking at was one click away, which is the same distance to
 * both and therefore no distance to either.
 *
 * Three groups, in the order they are asked: how it looks (theme, density, font,
 * motion), what the server is actually configured with (read-only, because a
 * settings panel that could edit a running server's environment is a panel with no
 * tests), and the one thing a browser cannot discover for itself -- the API token.
 * The token box is here because when `iris web` is started with `IRIS_API_TOKEN` the
 * API deliberately reports only *whether* one is configured, never its value; the
 * field is a place to paste what the operator already knows, kept in `localStorage`
 * and sent as `X-IRIS-Token`.
 *
 * Everything in the appearance group writes to exactly one place, the browser's own
 * `localStorage`. It never asks the server, because the server has no opinion about
 * how a viewer wants their dashboard coloured.
 */
export function SettingsSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const panelRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (!open) return
    // Capture on `window`, which is the first node the event reaches, so this runs
    // before the document-level handlers the launch window and the terminal page
    // register. Escape therefore closes *this* sheet and nothing else.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      onClose()
    }
    window.addEventListener('keydown', onKeyDown, true)
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Node && panelRef.current?.contains(target)) return
      onClose()
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    return () => {
      window.removeEventListener('keydown', onKeyDown, true)
      document.removeEventListener('pointerdown', onPointerDown, true)
    }
  }, [onClose, open])

  if (!open) return null

  return (
    <>
      {/* Click-away without a visible scrim: the sheet is not modal, so dimming the
          page behind it would claim the work stopped when it did not. */}
      <div className="fixed inset-0 z-[44]" role="presentation" />
      <aside
        ref={panelRef}
        role="dialog"
        aria-label="外观与界面设置"
        className="glass fixed bottom-footer right-3 z-[45] flex max-h-[calc(100vh-2.5rem)] w-[380px] max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden rounded-panel shadow-glass"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-surface-border px-4 py-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-card bg-iris-500/12 text-iris-400">
            <Palette className="h-4 w-4" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-ink-100">外观与界面设置</h2>
            <p className="mt-0.5 text-[10px] leading-relaxed text-ink-500">
              主题、密度与动效保存在本机浏览器；有效配置与令牌来自服务端
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded p-1 text-ink-500 transition-colors hover:bg-surface-hover hover:text-ink-100"
            title="关闭"
            aria-label="关闭设置"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>

        <div className="scroll-y flex min-h-0 flex-1 flex-col gap-3 p-3">
          <AppearanceGroup />
          <ConfigGroup />
          <TokenGroup />
        </div>
      </aside>
    </>
  )
}

/* ------------------------------------------------------------------ appearance */

/** Theme, density, interface font, motion.
 *
 * The theme swatches are three colours rather than a screenshot: a screenshot per
 * theme would have to be regenerated by hand whenever the palette changes, and a
 * stale screenshot is a promise the page cannot keep. Three swatches -- base,
 * raised surface, accent -- are enough to tell two dark themes apart, and the
 * rendered theme itself is one click away.
 *
 * The three light themes are the ones a screenshot sells worst and this sells best:
 * the contrast between their dark siblings is not a hue difference, it is a
 * legibility difference, and only a live switch shows it.
 */
function AppearanceGroup() {
  const theme = useAppearanceStore((state) => state.theme)
  const density = useAppearanceStore((state) => state.density)
  const font = useAppearanceStore((state) => state.font)
  const animations = useAppearanceStore((state) => state.animations)
  const setTheme = useAppearanceStore((state) => state.setTheme)
  const setDensity = useAppearanceStore((state) => state.setDensity)
  const setFont = useAppearanceStore((state) => state.setFont)
  const setAnimations = useAppearanceStore((state) => state.setAnimations)

  const current = THEMES.find((item) => item.id === theme) ?? THEMES[0]
  const light = isLightBackdrop(current?.colours[0] ?? '#0a0d14')

  return (
    <>
      <div className="flex items-center gap-1.5 text-[10px] font-medium text-ink-500">
        {light ? <Sun className="h-3 w-3" aria-hidden="true" /> : <Moon className="h-3 w-3" aria-hidden="true" />}
        当前主题 · {current?.name ?? DASH}
      </div>

      <div className="grid grid-cols-2 gap-2">
        {THEMES.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-pressed={theme === item.id}
            onClick={() => setTheme(item.id)}
            title={item.description}
            className={classNames(
              'rounded-card border p-2 text-left transition-colors',
              theme === item.id
                ? 'border-iris-400/60 bg-iris-500/10'
                : 'border-surface-border bg-surface-sunken/60 hover:border-surface-border-strong hover:bg-surface-hover',
            )}
          >
            <span className="mb-1.5 flex h-5 overflow-hidden rounded border border-surface-border" aria-hidden="true">
              {item.colours.map((colour, index) => (
                <span key={colour} className="flex-1" style={{ backgroundColor: colour, opacity: index === 2 ? 0.95 : 1 }} />
              ))}
            </span>
            <span className="flex items-center gap-1 text-2xs font-medium text-ink-100">
              {theme === item.id && <Check className="h-3 w-3 shrink-0 text-iris-400" aria-hidden="true" />}
              <span className="truncate">{item.name}</span>
            </span>
            {/* Clamped, because a twelve-swatch grid with unwrapped descriptions is a
                grid of twelve different heights. The full text is the tooltip. */}
            <span className="mt-0.5 block line-clamp-2 text-[10px] leading-relaxed text-ink-500">
              {item.description}
            </span>
          </button>
        ))}
      </div>

      <section className="settings-group">
        <h3 className="settings-group-title">界面密度</h3>
        <p className="settings-group-hint">缩放字号与间距，面板宽度与终端 80×24 等固定尺寸不受影响</p>
        <ChoiceGroup<Density>
          options={DENSITIES}
          value={density}
          labels={DENSITY_LABELS}
          onChange={setDensity}
        />
      </section>

      <section className="settings-group">
        <h3 className="settings-group-title">界面字体</h3>
        <p className="settings-group-hint">显式标注等宽或衬线的区域（运行 ID、终端旁注）始终保持等宽</p>
        <ChoiceGroup<UiFont>
          options={UI_FONTS}
          value={font}
          labels={FONT_LABELS}
          onChange={setFont}
        />
      </section>

      <section className="settings-group">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="settings-group-title">动态效果</h3>
            <p className="settings-group-hint">
              状态光环、加载过渡与悬停动画；系统的「减弱动态效果」设置始终优先
            </p>
          </div>
          {/* A switch, not a button that says "on/off": the control says which state it
              is in and you move it to the other one, which is the whole grammar of a
              switch. `aria-pressed` carries the state for anyone not looking at it. */}
          <button
            type="button"
            role="switch"
            aria-checked={animations}
            aria-label="动态效果"
            onClick={() => setAnimations(!animations)}
            className={classNames(
              'relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors',
              animations ? 'bg-iris-500' : 'bg-surface-hover',
            )}
          >
            <span
              className={classNames(
                'absolute left-0.5 top-0.5 size-4 rounded-full bg-ink-100 transition-transform duration-150',
                animations && 'translate-x-4',
              )}
              aria-hidden="true"
            />
          </button>
        </div>
      </section>
    </>
  )
}

/* ---------------------------------------------------------------------- config */

/** What the server resolved, read-only. `read_only` is a fact about the service, so
 *  it is shown as a row rather than implied by the absence of edit controls. */
function ConfigGroup() {
  const config = useConfig()

  return (
    <section className="settings-group">
      <div className="flex items-center justify-between gap-2">
        <h3 className="settings-group-title">有效配置（只读）</h3>
        <Button size="sm" variant="ghost" onClick={() => void config.refetch()} title="重新读取">
          <RefreshCw className="h-3 w-3" aria-hidden="true" />
          重读
        </Button>
      </div>
      <p className="settings-group-hint">来自 .env 与 IRIS_* 环境变量，修改后需重启 iris web</p>

      {config.isLoading && <Skeleton className="mt-2 h-16 w-full" />}
      {config.isError && <ErrorState title="无法读取配置" detail={(config.error as Error).message} />}
      {config.data && (
        <div className="mt-1.5 flex flex-col">
          <DataRow label="数据库" mono>
            {config.data.database_url}
          </DataRow>
          <DataRow label="暂存目录" mono>
            {config.data.scratch_dir}
          </DataRow>
          <DataRow label="上传上限" mono>
            {config.data.api_max_upload_mb} MB
          </DataRow>
          <DataRow label="令牌">
            <Badge tone={config.data.api_token_configured ? 'violet' : 'neutral'}>
              {config.data.api_token_configured ? '已配置' : '未配置（本地模式）'}
            </Badge>
          </DataRow>
          <DataRow label="写权限">
            <Badge tone={config.data.read_only ? 'neutral' : 'success'}>
              {config.data.read_only ? '只读' : '可写'}
            </Badge>
          </DataRow>
          <p className="mt-1 text-[10px] leading-relaxed text-ink-700">{config.data.note}</p>
        </div>
      )}
    </section>
  )
}

/* ----------------------------------------------------------------------- token */

function TokenGroup() {
  const [token, setToken] = useState('')
  const [probe, setProbe] = useState<'idle' | 'checking' | 'ok' | 'bad'>('idle')
  const [detail, setDetail] = useState<string | null>(null)

  useEffect(() => {
    setToken(readToken())
  }, [])

  const apply = async () => {
    writeToken(token.trim())
    setProbe('checking')
    try {
      await api.stats()
      setProbe('ok')
      setDetail('令牌可用，统计已可读取')
    } catch (error) {
      setProbe('bad')
      setDetail(error instanceof ApiError ? error.message : String(error))
    }
  }

  return (
    <section className="settings-group">
      <div className="flex items-center justify-between gap-2">
        <h3 className="settings-group-title">API 令牌</h3>
        <Badge tone={probe === 'ok' ? 'success' : probe === 'bad' ? 'danger' : 'neutral'}>
          {probe === 'ok' ? '已验证' : probe === 'bad' ? '验证失败' : probe === 'checking' ? '验证中' : '未验证'}
        </Badge>
      </div>
      <p className="settings-group-hint">仅当服务以 IRIS_API_TOKEN 启动时才需要</p>

      {/* The buttons sit *below* the field rather than beside it. Beside it, the
          380px column left the input about 200px wide, which is enough for `••••`
          and nothing else -- pasting a token means either a very short field or
          scrolling inside it, and the two buttons are what make the panel wide in
          the first place. Stacked, the field spans the column and the buttons form
          one row under it, so the widest thing in the group is the thing you type
          into. */}
      <div className="mt-2">
        <TextInput
          label="令牌"
          type="password"
          autoComplete="off"
          placeholder="与 IRIS_API_TOKEN 相同的值"
          value={token}
          onChange={(event) => {
            setToken(event.target.value)
            setProbe('idle')
          }}
        />
        <div className="mt-2 flex items-center gap-1.5">
          <Button variant="primary" onClick={() => void apply()} disabled={probe === 'checking'}>
            <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
            保存并验证
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setToken('')
              writeToken('')
              setProbe('idle')
              setDetail('已清除本地令牌')
            }}
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
            清除
          </Button>
        </div>
      </div>
      {detail && (
        <p className="mt-1.5 flex items-center gap-1 text-2xs" role="status">
          {probe === 'ok' ? (
            <Check className="h-3 w-3 shrink-0 text-success" aria-hidden="true" />
          ) : (
            <CircleHelp className="h-3 w-3 shrink-0 text-warning" aria-hidden="true" />
          )}
          <span className={probe === 'ok' ? 'text-success' : 'text-warning'}>{detail}</span>
        </p>
      )}
      <ul className="mt-2 flex flex-col gap-1 border-t border-surface-border pt-2 text-[10px] leading-relaxed text-ink-700">
        <li>· 令牌保存在本浏览器的 localStorage，仅作为 X-IRIS-Token 请求头发送</li>
        <li>· 终端 WebSocket 例外：浏览器无法自定义头，该通道用查询参数传令牌，服务端用同一套常量时间比较校验</li>
        <li>· 服务端永远不会通过 API 返回令牌本身，/api/v1/config 只报告「是否已配置」</li>
      </ul>
    </section>
  )
}

/* ----------------------------------------------------------------------- bits */

/** A row of mutually exclusive buttons. `aria-pressed` rather than a radio group
 *  because these are switches that act immediately -- there is nothing to submit,
 *  and a radio group implies a form that has to be confirmed. */
function ChoiceGroup<T extends string>({
  options,
  value,
  labels,
  onChange,
}: {
  options: readonly T[]
  value: T
  labels: Record<T, string>
  onChange: (value: T) => void
}) {
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={value === option}
          onClick={() => onChange(option)}
          className={classNames(
            'rounded border px-2 py-1 text-2xs transition-colors',
            value === option
              ? 'border-iris-400/60 bg-iris-500/10 text-iris-400'
              : 'border-surface-border text-ink-500 hover:border-surface-border-strong hover:text-ink-300',
          )}
        >
          {labels[option]}
        </button>
      ))}
    </div>
  )
}

/** Whether a swatch reads as a light backdrop. Judged from the base colour's own
 *  luminance rather than from a hand-maintained list of light theme ids, because a
 *  fourteenth theme would otherwise default to the wrong icon and nothing would
 *  notice. */
function isLightBackdrop(colour: string): boolean {
  const hex = colour.replace('#', '')
  if (hex.length !== 6) return false
  const r = Number.parseInt(hex.slice(0, 2), 16)
  const g = Number.parseInt(hex.slice(2, 4), 16)
  const b = Number.parseInt(hex.slice(4, 6), 16)
  // Rec. 601 luma, the same weighting the eye roughly applies.
  return (r * 299 + g * 587 + b * 114) / 1000 > 140
}
