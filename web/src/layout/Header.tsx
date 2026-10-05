import { Link, useLocation } from 'react-router-dom'
import { Activity, Command, PanelRightClose, PanelRightOpen, Server, Settings as SettingsIcon } from 'lucide-react'

import { Badge, Button, StatusDot } from '../components/ui'
import { classNames } from '../lib/format'
import { useCapabilities, useConfig, useEmulations, useStats } from '../hooks/queries'

import { useUiStore } from '../store/ui'

/**
 * The top bar: what this server is, whether it is authenticated, and how many
 * instances are live.
 *
 * The version and the token state are here because they are the two facts a viewer
 * asks for first and cannot otherwise see -- "am I looking at the current build" and
 * "am I looking at everything or only what I own".
 */
export function Header({ onOpenPalette }: { onOpenPalette: () => void }) {
  const stats = useStats()
  const emulations = useEmulations()
  const config = useConfig()
  const capabilities = useCapabilities()
  const inspectorOpen = useUiStore((state) => state.inspectorOpen)
  const setInspectorOpen = useUiStore((state) => state.setInspectorOpen)
  // No ticker here: nothing on this row is time-dependent. The live "up for"
  // labels live in the inspector and the instance tab, where they own a tick.

  const tokenRequired = config.data?.api_token_configured ?? false

  return (
    <header className="flex h-header shrink-0 items-center gap-3 border-b border-surface-border bg-surface-sunken/70 px-3 backdrop-blur-glass">
      <div className="flex items-center gap-2">
        <Server className="h-4 w-4 text-iris-400" aria-hidden="true" />
        {/* The one serif in the product: a logotype set in the UI font reads as a
            heading, and this row already has a heading next to it. */}
        <h1 className="brand-wordmark text-base">IRIS</h1>
        <span className="text-sm font-medium tracking-tight text-ink-300">固件仿真工作台</span>
        <Badge tone="neutral" className="hidden sm:inline-flex" title="后端与工作台共用的版本号">
          {capabilities.data ? `v${capabilities.data.version}` : '—'}
        </Badge>
      </div>

      <div className="ml-2 hidden items-center gap-3 md:flex">
        <Badge tone={tokenRequired ? 'violet' : 'neutral'} title={config.data?.note}>
          {tokenRequired ? '已启用令牌' : '本地模式（无令牌）'}
        </Badge>
        <span className="flex items-center gap-1.5 text-2xs text-ink-500" title="本服务正在托管的仿真实例数">
          <StatusDot tone={emulations.data?.length ? 'success' : 'neutral'} pulse={Boolean(emulations.data?.length)} />
          <span className="tnum">{emulations.data?.length ?? '—'}</span> 个实例运行中
        </span>
        <span className="flex items-center gap-1.5 text-2xs text-ink-500" title="历史累计 Web 可达率（全库口径）">
          <Activity className="h-3 w-3" aria-hidden="true" />
          <span className="tnum">
            {stats.data?.web_reach_rate != null ? `${(stats.data.web_reach_rate * 100).toFixed(1)}%` : '—'}
          </span>
          历史可达
        </span>
      </div>

      <div className="ml-auto flex items-center gap-2">
        {/* The one place search lives. It used to be a box at the top of the rail as
            well, which meant two entries for the same palette on one screen; the
            header keeps the shortcut visible on every page and the rail keeps its
            top edge for the thing the rail is for. */}
        <Button size="sm" variant="ghost" onClick={onOpenPalette} title="命令面板 (Ctrl+K)">
          <Command className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="hidden lg:inline">命令面板</span>
          <kbd className="hidden rounded border border-surface-border px-1 font-mono text-[10px] text-ink-700 lg:inline">
            Ctrl K
          </kbd>
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setInspectorOpen(!inspectorOpen)}
          title={inspectorOpen ? '关闭检查器' : '打开检查器'}
          aria-label={inspectorOpen ? '关闭检查器' : '打开检查器'}
          aria-pressed={inspectorOpen}
        >
          {inspectorOpen ? (
            <PanelRightClose className="h-4 w-4" aria-hidden="true" />
          ) : (
            <PanelRightOpen className="h-4 w-4" aria-hidden="true" />
          )}
        </Button>
      </div>
    </header>
  )
}

/**
 * The status line.
 *
 * Carries the one fact that is otherwise invisible and expensive to discover: the
 * console channel's real capability, read from the server. On a build without the
 * chardev change the terminal page cannot work, and saying so in the footer is
 * cheaper than letting a reviewer type into a dead terminal.
 */
export function Footer() {
  const stats = useStats()
  const emulations = useEmulations()
  const onSettings = useLocation().pathname === '/settings'

  return (
    <footer
      className={classNames(
        'flex h-footer shrink-0 items-center gap-3 border-t border-surface-border bg-surface-sunken/70 px-3',
        'text-2xs text-ink-700',
      )}
    >
      <span className="tnum">
        记录 {stats.data?.total ?? '—'} 条 · Web 可达 {stats.data?.web_ok ?? '—'} 条
      </span>
      <span aria-hidden="true">·</span>
      <span>终端通道：交互式串口（QEMU chardev，尺寸固定 80×24）</span>
      <span className="ml-auto flex items-center gap-3">
        <span className="hidden md:inline">数据口径：全库累计，含同一固件的多次运行</span>
        <span aria-hidden="true" className="hidden md:inline">·</span>
        {/* A plain anchor, not a router link: /docs is FastAPI's OpenAPI page served
            by the backend, so a client-side navigation would land on the router's
            catch-all and bounce back to the dashboard. */}
        <a href="/docs" target="_blank" rel="noreferrer" className="transition-colors hover:text-ink-300">
          API 文档
        </a>
        <span aria-hidden="true">·</span>
        {/* Settings lives at the far right of the bottom bar and is an icon alone.
            It is the one control on screen that changes nothing about the work, so
            it does not belong among the rail's pages -- but it does have to stay
            reachable from anywhere, which is why it sits on the bar every screen
            already has. `aria-current` rather than a filled background, because
            without a label a filled square is a state nobody can read. */}
        <Link
          to="/settings"
          title="界面设置与主题"
          aria-label="设置"
          aria-current={onSettings ? 'page' : undefined}
          className={classNames(
            'grid h-6 w-6 place-items-center rounded transition-colors hover:bg-surface-hover hover:text-ink-100',
            onSettings ? 'text-iris-400' : 'text-ink-500',
          )}
        >
          <SettingsIcon className="h-3.5 w-3.5" aria-hidden="true" />
        </Link>
      </span>
      {emulations.isError && (
        <span className="text-danger" role="status">
          实例列表不可用
        </span>
      )}
    </footer>
  )
}