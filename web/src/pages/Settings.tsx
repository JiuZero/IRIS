import { useEffect, useState } from 'react'
import { Check, CircleHelp, KeyRound, Moon, RefreshCw, Sun, X } from 'lucide-react'

import { Badge, Button, DataRow, ErrorState, Panel, Skeleton, TextInput } from '../components/ui'
import { ApiError, api, readToken, writeToken } from '../lib/api'
import { classNames } from '../lib/format'
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

/** Read-only settings, plus the two things a browser genuinely cannot discover:
 * the token, and its own appearance.
 *
 * Three panels, and it used to be five. The capability census and the limits list
 * moved to `/work-policy`: they answer "what is in the box and what is not", which
 * is a question about the *product*, asked from the rail, not a preference to be
 * adjusted next to the theme swatches. Keeping them here meant the same census was
 * written out in two places, which is two places to forget to update.
 *
 * The token box is here because when `iris web` is started with `IRIS_API_TOKEN`
 * the pages have no way to obtain it -- the API deliberately reports only *whether*
 * one is configured, never its value. So the field is a place to paste what the
 * operator already knows, stored in `localStorage` and sent as `X-IRIS-Token`.
 *
 * The appearance panel is the one part of this page that does write somewhere, and
 * it writes to exactly one place: the browser's own `localStorage`. It never asks
 * the server, because the server has no opinion about how a viewer wants their
 * dashboard coloured, and a settings page that could change a running server's
 * behaviour is a settings page with no tests.
 */
export function Settings() {
  const config = useConfig()
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
    <div className="grid grid-cols-1 gap-4 p-4 xl:grid-cols-2">
      <AppearancePanel />

      <Panel
        title="有效配置（只读）"
        subtitle="来自 .env 与 IRIS_* 环境变量，修改后需重启 iris web"
        actions={
          <Button size="sm" variant="ghost" onClick={() => void config.refetch()}>
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            重新读取
          </Button>
        }
      >
        {config.isLoading && <Skeleton className="h-24 w-full" />}
        {config.isError && <ErrorState title="无法读取配置" detail={(config.error as Error).message} />}
        {config.data && (
          <>
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
              <Badge tone="neutral">只读</Badge>
            </DataRow>
            <p className="mt-2 text-[10px] leading-relaxed text-ink-700">{config.data.note}</p>
          </>
        )}
      </Panel>

      <Panel
        title="API 令牌"
        subtitle="仅当服务以 IRIS_API_TOKEN 启动时才需要"
        actions={
          <Badge tone={probe === 'ok' ? 'success' : probe === 'bad' ? 'danger' : 'neutral'}>
            {probe === 'ok' ? '已验证' : probe === 'bad' ? '验证失败' : probe === 'checking' ? '验证中' : '未验证'}
          </Badge>
        }
      >
        <div className="flex items-end gap-2">
          <div className="flex-1">
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
          </div>
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
        {detail && (
          <p className="mt-2 flex items-center gap-1 text-2xs" role="status">
            {probe === 'ok' ? (
              <Check className="h-3 w-3 text-success" aria-hidden="true" />
            ) : (
              <CircleHelp className="h-3 w-3 text-warning" aria-hidden="true" />
            )}
            <span className={probe === 'ok' ? 'text-success' : 'text-warning'}>{detail}</span>
          </p>
        )}
        <ul className="mt-3 flex flex-col gap-1 border-t border-surface-border pt-2 text-[10px] leading-relaxed text-ink-700">
          <li>· 令牌保存在本浏览器的 localStorage，仅作为 X-IRIS-Token 请求头发送</li>
          <li>· 终端 WebSocket 例外：浏览器无法自定义头，该通道用查询参数传令牌，服务端用同一套常量时间比较校验</li>
          <li>· 服务端永远不会通过 API 返回令牌本身，/api/v1/config 只报告"是否已配置"。</li>
        </ul>
      </Panel>
    </div>
  )
}

/** A row of mutually exclusive buttons. `aria-pressed` rather than a radio group
 *  because these are switches that act immediately -- there is nothing to submit,
 *  and a radio group implies a form that has to be confirmed. */
function Choice<T extends string>({
  label,
  hint,
  options,
  value,
  labels,
  onChange,
}: {
  label: string
  hint: string
  options: readonly T[]
  value: T
  labels: Record<T, string>
  onChange: (value: T) => void
}) {
  return (
    <div className="border-t border-surface-border pt-3">
      <div className="text-2xs font-medium text-ink-300">{label}</div>
      <p className="mt-0.5 text-[10px] leading-relaxed text-ink-500">{hint}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {options.map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={value === option}
            onClick={() => onChange(option)}
            className={classNames(
              'rounded-card border px-2 py-1 text-2xs transition-colors',
              value === option
                ? 'border-iris-400/60 bg-iris-500/12 text-iris-400'
                : 'border-surface-border text-ink-500 hover:border-surface-border-strong hover:text-ink-300',
            )}
          >
            {labels[option]}
          </button>
        ))}
      </div>
    </div>
  )
}

/**
 * Theme, density, interface font, motion.
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
function AppearancePanel() {
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
    <Panel
      title="外观"
      subtitle="主题、密度、字体与动效；仅保存在当前浏览器"
      actions={
        <Badge tone="iris" icon={light ? <Sun className="h-3 w-3" aria-hidden="true" /> : <Moon className="h-3 w-3" aria-hidden="true" />}>
          {current?.name}
        </Badge>
      }
    >
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {THEMES.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-pressed={theme === item.id}
            onClick={() => setTheme(item.id)}
            className={classNames(
              'rounded-card border p-2 text-left transition-colors',
              theme === item.id
                ? 'border-iris-400/60 bg-iris-500/10'
                : 'border-surface-border hover:border-surface-border-strong hover:bg-surface-hover',
            )}
          >
            <span className="mb-1.5 flex h-5 overflow-hidden rounded border border-surface-border" aria-hidden="true">
              {item.colours.map((colour, index) => (
                <span
                  key={colour}
                  className="flex-1"
                  style={{ backgroundColor: colour, opacity: index === 2 ? 0.95 : 1 }}
                />
              ))}
            </span>
            <span className="flex items-center gap-1 text-2xs font-medium text-ink-100">
              {theme === item.id && <Check className="h-3 w-3 text-iris-400" aria-hidden="true" />}
              {item.name}
            </span>
            <span className="mt-0.5 block text-[10px] leading-relaxed text-ink-500">{item.description}</span>
          </button>
        ))}
      </div>

      <div className="mt-4 flex flex-col gap-3">
        <Choice<Density>
          label="界面密度"
          hint="缩放字号与间距。面板宽度、终端 80×24 等固定尺寸不受影响"
          options={DENSITIES}
          value={density}
          labels={DENSITY_LABELS}
          onChange={setDensity}
        />
        <Choice<UiFont>
          label="界面字体"
          hint="显式标注等宽或衬线的区域（运行 ID、终端旁注）始终保持等宽"
          options={UI_FONTS}
          value={font}
          labels={FONT_LABELS}
          onChange={setFont}
        />
        <div className="border-t border-surface-border pt-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-2xs font-medium text-ink-300">动态效果</div>
              <p className="mt-0.5 text-[10px] leading-relaxed text-ink-500">
                状态光环、加载过渡与悬停动画。关闭后会一并停用文档内全部动画；
                系统的「减弱动态效果」设置始终优先于此开关。
              </p>
            </div>
            <Button
              size="sm"
              variant={animations ? 'outline' : 'primary'}
              onClick={() => setAnimations(!animations)}
              aria-pressed={animations}
              aria-label={animations ? '关闭动态效果' : '开启动态效果'}
            >
              {animations ? '已开启' : '已关闭'}
            </Button>
          </div>
        </div>
      </div>
    </Panel>
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