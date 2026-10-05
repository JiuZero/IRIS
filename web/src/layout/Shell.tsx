import { useMemo, useState } from 'react'
import { Outlet, useNavigate, useParams } from 'react-router-dom'

import { CommandPalette } from '../components/CommandPalette'
import { LaunchDialog } from '../components/LaunchDialog'
import { useBreakpoint } from '../hooks'
import { useHotkeys, type Hotkey } from '../hooks/hotkeys'
import { Inspector } from './Inspector'
import { Footer, Header } from './Header'
import { LogPanel, LogPanelToggle } from './LogPanel'
import { Resizer } from './Resizer'
import { Sidebar } from './Sidebar'
import { PANEL_BOUNDS, useUiStore } from '../store/ui'

/**
 * The five-region shell: header, sidebar, main, inspector, footer.
 *
 * How it collapses, and why:
 *
 *  - `>= 1440` all three columns, sidebar and inspector both resizable.
 *  - `1200-1439` the inspector is still there but starts closed -- the content area
 *    is what the demo is about, and a 340px panel on a 1280px screen costs a third
 *    of it.
 *  - `1024-1199` the inspector becomes an overlay, so it can cover the content the
 *    user explicitly asked for instead of being permanently absent.
 *  - `< 1024` both panels are overlays. A terminal at 80 columns needs ~640px to be
 *    usable at all, and a three-column layout on a phone leaves it nothing.
 *
 * The widths are persisted per panel, so the layout somebody arranged for their
 * monitor is still there tomorrow.
 */
export function Shell() {
  const band = useBreakpoint()
  const [paletteOpen, setPaletteOpen] = useState(false)
  const params = useParams()
  const navigate = useNavigate()
  const routeIid = params.iid ? Number.parseInt(params.iid, 10) : null
  const pinned = useUiStore((state) => state.pinnedInstance)
  const iid = routeIid ?? pinned
  const launchOpen = useUiStore((state) => state.launchOpen)
  const closeLaunch = useUiStore((state) => state.closeLaunch)

  const collapsed = useUiStore((state) => state.sidebarCollapsed)
  const sidebarWidth = useUiStore((state) => state.sidebarWidth)
  const inspectorWidth = useUiStore((state) => state.inspectorWidth)
  const inspectorOpen = useUiStore((state) => state.inspectorOpen)
  const setInspectorOpen = useUiStore((state) => state.setInspectorOpen)
  const setPanelWidth = useUiStore((state) => state.setPanelWidth)
  const toggleSidebar = useUiStore((state) => state.toggleSidebar)
  const toggleLogPanel = useUiStore((state) => state.toggleLogPanel)

  // Below 1200 the inspector is an overlay, so it is forced shut rather than left
  // open as an empty 340px column.
  const inspectorAsOverlay = band === 'laptop' || band === 'narrow'
  const inspectorVisible = !inspectorAsOverlay && inspectorOpen

  const hotkeys = useMemo<Hotkey[]>(
    () => [
      { combo: 'mod+k', description: '命令面板', handler: () => setPaletteOpen((open) => !open) },
      { combo: 'mod+b', description: '收起/展开侧栏', handler: toggleSidebar },
      { combo: 'mod+j', description: '失败抽屉', handler: toggleLogPanel },
      {
        combo: 'mod+i',
        description: '切换检查器',
        handler: () => setInspectorOpen(!useUiStore.getState().inspectorOpen),
      },
      {
        combo: 'mod+`',
        description: '回到终端',
        handler: () => {
          // Client-side navigation, not a full page load: reloading would tear down
          // the terminal socket and re-fetch every query to reach a screen the
          // router already knows how to render.
          const target = routeIid ?? useUiStore.getState().pinnedInstance
          if (target !== null) navigate(`/instances/${target}/terminal`)
        },
      },
    ],
    [navigate, routeIid, setInspectorOpen, toggleLogPanel, toggleSidebar],
  )
  useHotkeys(hotkeys)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Header onOpenPalette={() => setPaletteOpen(true)} />

      <div className="flex min-h-0 flex-1">
        {!collapsed && (
          <>
            <div style={{ width: sidebarWidth }} className="shrink-0">
              <Sidebar onOpenPalette={() => setPaletteOpen(true)} />
            </div>
            {band !== 'narrow' && (
              <Resizer
                orientation="vertical"
                value={sidebarWidth}
                min={PANEL_BOUNDS.sidebar.min}
                max={PANEL_BOUNDS.sidebar.max}
                label="侧栏宽度"
                onChange={(value) => setPanelWidth('sidebar', value)}
              />
            )}
          </>
        )}

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-8 shrink-0 items-center gap-2 border-b border-surface-border px-3">
            <LogPanelToggle />
            {collapsed && (
              <button
                type="button"
                onClick={toggleSidebar}
                className="text-2xs text-ink-500 transition-colors hover:text-ink-300"
              >
                展开侧栏 (Ctrl+B)
              </button>
            )}
          </div>
          <div className="scroll-y min-h-0 flex-1">
            <Outlet />
          </div>
          <LogPanel />
        </main>

        {inspectorVisible && (
          <>
            <Resizer
              orientation="vertical"
              value={inspectorWidth}
              min={PANEL_BOUNDS.inspector.min}
              max={PANEL_BOUNDS.inspector.max}
              label="检查器宽度"
              direction={-1}
              onChange={(value) => setPanelWidth('inspector', value)}
            />
            <div style={{ width: inspectorWidth }} className="shrink-0">
              <Inspector iid={iid} />
            </div>
          </>
        )}

        {inspectorAsOverlay && inspectorOpen && (
          <div className="fixed inset-y-header right-0 z-40 w-[340px] max-w-[85vw] shadow-glass">
            <Inspector iid={iid} />
            <button
              type="button"
              onClick={() => setInspectorOpen(false)}
              className="absolute left-0 top-2 h-full w-1 cursor-col-resize bg-transparent hover:bg-iris-400/40"
              aria-label="关闭检查器"
            />
          </div>
        )}
      </div>

      <Footer />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <LaunchDialog open={launchOpen} onClose={closeLaunch} />
    </div>
  )
}