import { NavLink, useNavigate } from 'react-router-dom'
import {
  Cpu,
  Gauge,
  Hexagon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings as SettingsIcon,
  Terminal,
} from 'lucide-react'

import { Badge, StatusDot } from '../components/ui'
import { classNames } from '../lib/format'
import { useCapabilities, useEmulations } from '../hooks/queries'
import { useUiStore } from '../store/ui'

const NAV = [
  { to: '/', label: '总览', icon: Gauge, end: true },
  { to: '/instances', label: '实例', icon: Cpu, end: false },
  { to: '/settings', label: '设置', icon: SettingsIcon, end: false },
]

/**
 * The left rail: navigation, the running instances, and the capability summary.
 *
 * Instances are listed here rather than only on the list page because the shortcut
 * that matters most during a demo is "get back to the terminal I was in" -- and
 * that is a click on a name, not three clicks through a table.
 */
export function Sidebar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const collapsed = useUiStore((state) => state.sidebarCollapsed)
  const toggle = useUiStore((state) => state.toggleSidebar)
  const emulations = useEmulations()
  const capabilities = useCapabilities()
  const navigate = useNavigate()

  const running = emulations.data ?? []
  const unavailable = (capabilities.data?.items ?? []).filter((item) => item.state !== 'available')

  return (
    <nav
      aria-label="主导航"
      className={classNames(
        'flex h-full min-h-0 flex-col gap-2 border-r border-surface-border bg-surface-sunken/60 px-2 py-3 transition-[width] duration-200',
        collapsed ? 'w-14' : 'w-full',
      )}
    >
      <div className={classNames('flex items-center gap-2', collapsed ? 'justify-center' : 'px-1')}>
        <Hexagon className="h-5 w-5 shrink-0 text-iris-400" aria-hidden="true" />
        {!collapsed && <span className="text-sm font-semibold tracking-tight">IRIS</span>}
      </div>

      <button
        type="button"
        onClick={onOpenPalette}
        className={classNames(
          'flex h-8 items-center gap-2 rounded-card border border-surface-border bg-surface-faint text-2xs text-ink-500 transition-colors hover:border-iris-400/50 hover:text-ink-300',
          collapsed ? 'justify-center px-0' : 'px-2',
        )}
        title="命令面板 (Ctrl+K)"
        aria-label="打开命令面板"
      >
        <Search className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        {!collapsed && (
          <>
            <span className="flex-1 text-left">搜索与跳转</span>
            <kbd className="rounded border border-surface-border px-1 font-mono text-[10px] text-ink-700">Ctrl K</kbd>
          </>
        )}
      </button>

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
          <div className="nav-card mt-2 flex flex-col gap-1">
            <div className="flex items-center justify-between px-1">
              <span className="nav-card-title">运行中</span>
              <Badge tone={running.length ? 'success' : 'neutral'}>{running.length}</Badge>
            </div>
            <ul className="scroll-y flex min-h-0 flex-col gap-0.5">
              {running.length === 0 && (
                <li className="px-1 text-2xs leading-relaxed text-ink-700">
                  暂无实例，从 /instances 启动一次仿真即会出现在这里
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

          <div className="nav-card mt-auto flex flex-col gap-1 pt-2">
            <div className="flex items-center justify-between px-1">
              <span className="nav-card-title">能力</span>
              {capabilities.isLoading && <span className="text-2xs text-ink-700">读取中</span>}
            </div>
            <ul className="flex flex-col gap-0.5">
              {(capabilities.data?.items ?? []).slice(0, 6).map((item) => (
                <li key={item.id} className="flex items-center gap-2 px-1 py-0.5" title={item.detail}>
                  <StatusDot
                    tone={
                      item.state === 'available' ? 'success' : item.state === 'planned' ? 'warning' : 'danger'
                    }
                  />
                  <span className="flex-1 truncate text-2xs text-ink-300">{item.name}</span>
                  {item.state !== 'available' && (
                    <span className="text-[10px] text-ink-700">
                      {item.state === 'planned' ? '待改造' : '不含模型'}
                    </span>
                  )}
                </li>
              ))}
            </ul>
            {unavailable.length > 0 && (
              <p className="px-1 text-[10px] leading-relaxed text-ink-700">
                徽章状态由构建内容探测得出，详情见 /settings
              </p>
            )}
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