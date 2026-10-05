import { NavLink, useNavigate } from 'react-router-dom'
import {
  Activity,
  Boxes,
  Cpu,
  Gauge,
  ListChecks,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Terminal,
} from 'lucide-react'

import { Badge, Button, StatusDot } from '../components/ui'
import { classNames, DASH } from '../lib/format'
import { useEmulations, useSystem } from '../hooks/queries'
import { useUiStore } from '../store/ui'

/** The pages, in the order the questions get asked. Settings is deliberately not
 *  here: it is a bottom-bar icon now, because every entry on this rail is a question
 *  about the work, and "change the theme" is not one of them. */
const NAV = [
  { to: '/', label: '总览', icon: Gauge, end: true },
  { to: '/instances', label: '实例记录', icon: Cpu, end: false },
  { to: '/plugins', label: '插件中心', icon: Boxes, end: false },
  { to: '/work-policy', label: '工作策略', icon: ListChecks, end: false },
]

/**
 * The left rail: the primary action, navigation, the live instances, and what this
 * host is doing right now.
 *
 * Two decisions are load-bearing. The **create button is the first thing on the
 * rail**, because in this product creating an instance is the main thing anyone
 * comes to do; navigation is how you get there. And **nothing above it**: search
 * lives in the header's command palette, on Ctrl+K, and the wordmark lives in the
 * header too -- two copies of either on one screen is one more thing to read before
 * you get to the work, and the rail is the narrowest column on the page.
 *
 * The instance list is here rather than only on its own page because the shortcut
 * that matters during a demo is "get back to the terminal I was in" -- one click on
 * a name, not three clicks through a table.
 */
export function Sidebar() {
  const collapsed = useUiStore((state) => state.sidebarCollapsed)
  const toggle = useUiStore((state) => state.toggleSidebar)
  const openLaunch = useUiStore((state) => state.openLaunch)
  const emulations = useEmulations()
  const system = useSystem()
  const navigate = useNavigate()

  const running = emulations.data ?? []

  return (
    <nav
      aria-label="主导航"
      className={classNames(
        'flex h-full min-h-0 flex-col gap-2 border-r border-surface-border bg-surface-sunken/60 px-2 py-3 transition-[width] duration-200',
        collapsed ? 'w-14' : 'w-full',
      )}
    >
      {/* The one action the rail is built around, and the first control on it.
          Anchored at the top because a create button pinned to the bottom of a
          scrollable rail is the button nobody finds; on a collapsed rail it stays as
          the icon so the affordance survives at 56px. It opens a window rather than
          navigating, so launching works from any page -- including from the terminal
          somebody is about to leave. */}
      <Button
        variant="primary"
        onClick={openLaunch}
        className={classNames('w-full', collapsed && 'px-0')}
        title="新建实例"
      >
        <Plus className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        {!collapsed && <span>新建实例</span>}
        {!collapsed && <span className="sr-only">，在当前页面打开新建窗口</span>}
      </Button>

      <ul className="flex flex-col gap-0.5">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={end}
              title={collapsed ? label : undefined}
              className={() => classNames('nav-item', collapsed && 'justify-center px-0')}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              {!collapsed && <span className="truncate">{label}</span>}
            </NavLink>
          </li>
        ))}
      </ul>

      {!collapsed && (
        <>
          <div className="nav-card mt-2 flex min-h-0 flex-1 flex-col gap-1">
            <div className="flex items-center justify-between px-1">
              <span className="nav-card-title">最近实例</span>
              <Badge tone={running.length ? 'success' : 'neutral'}>{running.length}</Badge>
            </div>
            <ul className="scroll-y flex min-h-0 flex-col gap-0.5">
              {running.length === 0 && (
                <li className="px-1 text-2xs leading-relaxed text-ink-700">
                  当前没有托管中的实例。在上方「新建实例」启动一次，即会出现在这里
                </li>
              )}
              {running.map((item) => (
                <li key={item.iid}>
                  <button
                    type="button"
                    onClick={() => navigate(`/instances/${item.iid}`)}
                    className="flex w-full items-center gap-2 rounded-card px-2 py-1.5 text-left transition-colors hover:bg-surface-hover"
                  >
                    <StatusDot tone={item.web_ok ? 'success' : 'warning'} pulse={item.web_ok} />
                    <span className="tnum flex-1 truncate font-mono text-2xs text-ink-100">{item.iid}</span>
                    <span className="truncate text-2xs text-ink-500">{item.arch || '?'}</span>
                    {item.web_url && (
                      <Terminal className="h-3 w-3 shrink-0 text-ink-700" aria-label="可接入终端" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {/* What this host is doing right now: two bars narrow enough to read at a
              glance while a boot is running, which is exactly when the answer
              matters. This is now the *only* place the live host reading appears --
              it moved here from the dashboard strip, because "can this machine take
              another run" is a rail-sized question and a four-card-wide panel
              answering it pushed the cumulative record off the landing screen. */}
          <div className="nav-card flex flex-col gap-1.5">
            <div className="flex items-center justify-between px-1">
              <span className="nav-card-title">性能与内存</span>
              {system.isFetching && !system.isError && (
                <Activity className="h-3 w-3 text-iris-400 status-pulse" aria-hidden="true" />
              )}
            </div>
            <RailMeter
              label="CPU"
              value={system.data?.host.cpu_pct == null ? null : system.data.host.cpu_pct / 100}
            />
            <RailMeter
              label="内存"
              value={
                system.data?.host.mem_total_mb && system.data.host.mem_used_mb != null
                  ? system.data.host.mem_used_mb / system.data.host.mem_total_mb
                  : null
              }
            />
            <p className="px-1 text-[10px] leading-relaxed text-ink-700">
              {system.isError
                ? '采样不可用'
                : system.data?.host.cpu_pct == null && !system.data
                  ? '读取中'
                  : system.data?.host.cpu_pct == null
                    ? '首次采样无 CPU 窗口'
                    : `${system.data.host.cpu_cores ?? DASH} 核 · 内存 ${system.data.host.mem_used_mb ?? DASH} / ${
                        system.data.host.mem_total_mb ?? DASH
                      } MB`}
            </p>
          </div>

        </>
      )}

      <button
        type="button"
        onClick={toggle}
        className={classNames(
          'mt-1 flex h-8 items-center gap-2 rounded-card px-2 text-2xs text-ink-500 transition-colors hover:bg-surface-hover hover:text-ink-300',
          collapsed && 'justify-center px-0',
        )}
        title={collapsed ? '展开侧栏 (Ctrl+B)' : '收起侧栏 (Ctrl+B)'}
        aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
        aria-expanded={!collapsed}
      >
        {collapsed ? (
          <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
        ) : (
          <>
            <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
            <span>收起</span>
            <kbd className="ml-auto rounded border border-surface-border px-1 font-mono text-[10px] text-ink-700">
              Ctrl B
            </kbd>
          </>
        )}
      </button>
    </nav>
  )
}

/**
 * A one-line proportion bar for the rail.
 *
 * `Meter` would work but it carries a 2xs label/value pair meant for a card; at
 * 14px of rail width that wraps and pushes the capability census off screen. This
 * is the same measurement with the label and the number on one baseline.
 */
function RailMeter({ label, value }: { label: string; value: number | null }) {
  const measured = value != null && Number.isFinite(value)
  const ratio = measured ? Math.min(1, Math.max(0, value)) : 0
  return (
    <div className="flex items-center gap-2 px-1">
      <span className="w-7 shrink-0 text-[10px] text-ink-500">{label}</span>
      <span className="h-1 flex-1 overflow-hidden rounded-full bg-surface-hover">
        {measured && (
          <span
            className={classNames(
              'block h-full rounded-full',
              ratio >= 0.9 ? 'bg-danger' : ratio >= 0.75 ? 'bg-warning' : 'bg-iris-400',
            )}
            style={{ width: `${Math.round(ratio * 100)}%` }}
          />
        )}
      </span>
      {/* `DASH` and not `0.0%`: an unmeasured window is not an idle machine. */}
      <span className="tnum w-11 shrink-0 text-right text-[10px] text-ink-500">
        {measured ? `${(ratio * 100).toFixed(0)}%` : DASH}
      </span>
    </div>
  )
}