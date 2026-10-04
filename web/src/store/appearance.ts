/**
 * Appearance: theme, density, interface font, and whether anything moves.
 *
 * Four switches, one store, one effect. They are kept together rather than split
 * across a theme context and a density context because they are not independent
 * in practice -- "make the workbench denser and quieter" is a single decision, and
 * a person who made it should be able to undo it in one place.
 *
 * Persisted values are treated as untrusted input for the same reason the panel
 * widths are (see `store/ui.ts`): `localStorage` survives a browser update, a
 * hand edit, and a colleague's settings. Every value read back is checked against
 * the allowlist below, and an unknown one falls back to the default rather than
 * being written into `data-*` -- an unvalidated `data-theme="../../evil"` in the
 * document element is a hole, and a stale `iris.ui.appearance.theme=dark` from a
 * version that no longer has a `dark` theme must not blank the screen.
 */

import { create } from 'zustand'

const STORAGE_PREFIX = 'iris.ui.appearance.'

/** The theme ids. Each one must have a `:root[data-theme="..."]` block in
 *  `tokens.css`; `iris` is the default and is expressed by `:root` itself, so
 *  selecting it clears nothing rather than setting a name. */
export const THEME_IDS = [
  'iris',
  'midnight',
  'nord',
  'tokyo-night',
  'dracula',
  'gruvbox',
  'monokai',
  'daylight',
  'github-light',
  'aurora',
  'sunset',
  'ocean',
] as const

export type ThemeId = (typeof THEME_IDS)[number]

export const DENSITIES = ['compact', 'comfortable', 'spacious'] as const
export type Density = (typeof DENSITIES)[number]

export const UI_FONTS = ['sans', 'mono', 'serif'] as const
export type UiFont = (typeof UI_FONTS)[number]

export interface ThemeMeta {
  id: ThemeId
  name: string
  description: string
  /** Three swatches: base, raised surface, accent. Enough to tell two dark themes
   *  apart at a glance without rendering each one. */
  colours: [string, string, string]
}

/** Display metadata only. The colours live in `tokens.css`; these copies are for
 *  the picker swatches, and a value here that drifts from the stylesheet costs a
 *  preview, never the rendered theme. */
export const THEMES: ThemeMeta[] = [
  {
    id: 'iris',
    name: 'IRIS 深空',
    description: '品牌默认：蓝紫玻璃与两处径向光晕',
    colours: ['#0a0d14', '#171c2a', '#3563ff'],
  },
  {
    id: 'midnight',
    name: '午夜蓝',
    description: '近黑冷蓝基底，高对比，适合投影',
    colours: ['#05070c', '#101622', '#3d7dff'],
  },
  {
    id: 'nord',
    name: 'Nord',
    description: '低饱和北境蓝灰，长时间阅读护眼',
    colours: ['#2e3440', '#3b4252', '#81a1c1'],
  },
  {
    id: 'tokyo-night',
    name: '东京夜',
    description: '深蓝紫夜景，终端与日志阅读舒适',
    colours: ['#1a1b26', '#24283b', '#5c7ee8'],
  },
  {
    id: 'dracula',
    name: 'Dracula',
    description: '紫粉高对比开发者配色',
    colours: ['#282a36', '#44475a', '#a879f0'],
  },
  {
    id: 'gruvbox',
    name: 'Gruvbox',
    description: '复古暖棕，暗色暖调',
    colours: ['#282828', '#3c3836', '#d8a028'],
  },
  {
    id: 'monokai',
    name: 'Monokai',
    description: '高饱和霓虹，橙色主色避免与成功态混淆',
    colours: ['#272822', '#383830', '#fd971f'],
  },
  {
    id: 'daylight',
    name: '日光米',
    description: '浅色暖米，投影与明亮环境下可读',
    colours: ['#fbf6ec', '#fffdf5', '#2a4fe0'],
  },
  {
    id: 'github-light',
    name: 'GitHub 浅色',
    description: '中性白，文档与表格阅读',
    colours: ['#ffffff', '#f6f8fa', '#0757ba'],
  },
  {
    id: 'aurora',
    name: '极光渐变',
    description: '青蓝紫渐变背景，面板保持半透明',
    colours: ['#0f2027', '#203a43', '#38bdf8'],
  },
  {
    id: 'sunset',
    name: '落日渐变',
    description: '橙红紫暖色渐变背景',
    colours: ['#1a0a2e', '#4a1942', '#fb923c'],
  },
  {
    id: 'ocean',
    name: '深海渐变',
    description: '深蓝到青绿的海底渐变',
    colours: ['#020111', '#20124d', '#38bdf8'],
  },
]

export const DENSITY_LABELS: Record<Density, string> = {
  compact: '紧凑',
  comfortable: '舒适',
  spacious: '宽松',
}

export const FONT_LABELS: Record<UiFont, string> = {
  sans: '系统无衬线',
  mono: '等宽',
  serif: '衬线',
}


function readEnum<T extends string>(key: string, allow: readonly T[], fallback: T): T {
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + key)
    return raw !== null && (allow as readonly string[]).includes(raw) ? (raw as T) : fallback
  } catch {
    // Storage can be unavailable (private mode, disabled cookies). The defaults are
    // a working appearance, so there is nothing to report.
    return fallback
  }
}

function readBool(key: string, fallback: boolean): boolean {
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + key)
    if (raw === 'true') return true
    if (raw === 'false') return false
    return fallback
  } catch {
    return fallback
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(STORAGE_PREFIX + key, value)
  } catch {
    /* The preference simply will not persist; the session still looks right. */
  }
}

export interface AppearanceState {
  theme: ThemeId
  density: Density
  font: UiFont
  /** false renders `data-animations="off"`, which kills every transition and
   *  animation in the document -- not just the ones we know about. */
  animations: boolean
  setTheme: (theme: ThemeId) => void
  setDensity: (density: Density) => void
  setFont: (font: UiFont) => void
  setAnimations: (enabled: boolean) => void
}

export const useAppearanceStore = create<AppearanceState>((set) => ({
  theme: readEnum('theme', THEME_IDS, 'iris'),
  density: readEnum('density', DENSITIES, 'comfortable'),
  font: readEnum('font', UI_FONTS, 'sans'),
  animations: readBool('animations', true),

  setTheme: (theme) => {
    write('theme', theme)
    set({ theme })
  },
  setDensity: (density) => {
    write('density', density)
    set({ density })
  },
  setFont: (font) => {
    write('font', font)
    set({ font })
  },
  setAnimations: (animations) => {
    write('animations', animations ? 'true' : 'false')
    set({ animations })
  },
}))

/** Write the four attributes onto `<html>`, which is where `tokens.css` reads them.
 *
 *  Exported as a function rather than only performed inside an effect so that the
 *  first paint does not have to wait for React: `index.html` carries a short inline
 *  script that applies the persisted preferences before the bundle loads, and this
 *  is the function React re-applies on every change. Both write the same four
 *  attributes from the same four `localStorage` keys; if they ever disagree the
 *  symptom is a flash of the wrong theme on reload, which is why the key names are
 *  spelled out here rather than being reconstructed at the call site.
 */
export function applyAppearance(state: Pick<AppearanceState, 'theme' | 'density' | 'font' | 'animations'>): void {
  const root = document.documentElement
  if (state.theme === 'iris') delete root.dataset.theme
  else root.dataset.theme = state.theme
  root.dataset.density = state.density
  root.dataset.font = state.font
  root.dataset.animations = state.animations ? 'on' : 'off'
}