/**
 * UI state that outlives a page: panel widths, which instance is selected, and
 * the token.
 *
 * Persisted widths are stored per key and clamped on read, because a `localStorage`
 * value is not trustworthy input: it survives a browser update, a hand edit, and a
 * colleague's 4K monitor. An unclamped 900px sidebar would leave a 1280px screen
 * with 380px of content and no way to know why.
 *
 * This store deliberately holds *nothing* about emulations. Those come from the
 * server on every mount: a cached instance list is a list that lies after somebody
 * stops a container in another tab.
 */

import { create } from 'zustand'

const STORAGE_PREFIX = 'iris.ui.'

export const PANEL_KEYS = {
  sidebar: `${STORAGE_PREFIX}sidebar.w`,
  inspector: `${STORAGE_PREFIX}inspector.w`,
  logpanel: `${STORAGE_PREFIX}logpanel.w`,
} as const

/** Bounds in px. The minima are what keeps the content area usable; the maxima are
 *  what stops a stretched panel from pushing everything else off screen. */
export const PANEL_BOUNDS = {
  sidebar: { min: 200, max: 420, default: 264 },
  inspector: { min: 280, max: 480, default: 340 },
  logpanel: { min: 120, max: 480, default: 240 },
} as const

export type PanelName = keyof typeof PANEL_BOUNDS

function readWidth(name: PanelName): number {
  const bounds = PANEL_BOUNDS[name]
  try {
    const raw = window.localStorage.getItem(PANEL_KEYS[name])
    const parsed = raw === null ? NaN : Number.parseInt(raw, 10)
    if (!Number.isFinite(parsed)) return bounds.default
    return Math.min(bounds.max, Math.max(bounds.min, parsed))
  } catch {
    // Storage can be unavailable (private mode, disabled cookies). The default is
    // a working layout, so there is nothing to report.
    return bounds.default
  }
}

function writeWidth(name: PanelName, value: number): void {
  try {
    window.localStorage.setItem(PANEL_KEYS[name], String(Math.round(value)))
  } catch {
    /* The width simply will not persist; the session still works. */
  }
}

interface UiState {
  sidebarWidth: number
  inspectorWidth: number
  logPanelHeight: number
  inspectorOpen: boolean
  logPanelOpen: boolean
  /** null means "follow the route". Set by the command palette and the header. */
  pinnedInstance: number | null
  /** Whether the launch window is open. Held here rather than in the shell's own
   *  state because the three things that open it -- the rail's create button, the
   *  command palette, and the records page's empty state -- are not in one subtree,
   *  and the alternative is a callback threaded through three components to reach
   *  one boolean. Not persisted: a window that reopens on reload would hide the page
   *  somebody was reading. */
  launchOpen: boolean
  /** Whether the settings sheet is open. Same reasoning as `launchOpen`: the footer
   *  icon, the command palette, and the dashboard's auth-error shortcut live in
   *  three different subtrees, and the alternative is a callback threaded through
   *  all three to reach one boolean. Also not persisted, for the same reason. */
  settingsOpen: boolean
  setPanelWidth: (name: PanelName, value: number) => void
  resetPanelWidth: (name: PanelName) => void
  setInspectorOpen: (open: boolean) => void
  toggleLogPanel: () => void
  setLogPanelOpen: (open: boolean) => void
  setPinnedInstance: (iid: number | null) => void
  openLaunch: () => void
  closeLaunch: () => void
  openSettings: () => void
  closeSettings: () => void
}

export const useUiStore = create<UiState>((set, get) => ({
  sidebarWidth: readWidth('sidebar'),
  inspectorWidth: readWidth('inspector'),
  logPanelHeight: readWidth('logpanel'),
  inspectorOpen: true,
  logPanelOpen: false,
  pinnedInstance: null,
  launchOpen: false,
  settingsOpen: false,

  setPanelWidth: (name, value) => {
    const bounds = PANEL_BOUNDS[name]
    const clamped = Math.min(bounds.max, Math.max(bounds.min, Math.round(value)))
    if (name === 'sidebar') writeWidth('sidebar', clamped)
    if (name === 'inspector') writeWidth('inspector', clamped)
    if (name === 'logpanel') writeWidth('logpanel', clamped)
    if (name === 'sidebar') set({ sidebarWidth: clamped })
    if (name === 'inspector') set({ inspectorWidth: clamped })
    if (name === 'logpanel') set({ logPanelHeight: clamped })
  },

  resetPanelWidth: (name) => {
    const bounds = PANEL_BOUNDS[name]
    writeWidth(name, bounds.default)
    get().setPanelWidth(name, bounds.default)
  },

  setInspectorOpen: (open) => set({ inspectorOpen: open }),
  toggleLogPanel: () => set((state) => ({ logPanelOpen: !state.logPanelOpen })),
  setLogPanelOpen: (open) => set({ logPanelOpen: open }),
  setPinnedInstance: (iid) => set({ pinnedInstance: iid }),
  openLaunch: () => set({ launchOpen: true }),
  closeLaunch: () => set({ launchOpen: false }),
  openSettings: () => set({ settingsOpen: true }),
  closeSettings: () => set({ settingsOpen: false }),
}))

/** The viewport band a layout decision is made in. Derived once per resize rather
 *  than per component: three panels each calling `matchMedia` would each hold
 *  their own listener and drift apart for a frame. */
export type Breakpoint = 'wide' | 'desktop' | 'laptop' | 'narrow'

export function breakpointOf(width: number): Breakpoint {
  if (width >= 1440) return 'wide'
  if (width >= 1200) return 'desktop'
  if (width >= 1024) return 'laptop'
  return 'narrow'
}

export const BREAKPOINT_MIN: Record<Breakpoint, number> = {
  wide: 1440,
  desktop: 1200,
  laptop: 1024,
  narrow: 0,
}