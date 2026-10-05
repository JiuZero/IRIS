import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Boxes,
  Cpu,
  FileDown,
  Gauge,
  ListChecks,
  Palette,
  Plus,
  Search,
  Settings as SettingsIcon,
  Rows3,
  Terminal as TerminalIcon,
} from 'lucide-react'

import { Badge, StatusDot } from '../components/ui'
import { api } from '../lib/api'
import { classNames } from '../lib/format'
import { useEmulations } from '../hooks/queries'
import { DENSITIES, THEMES, useAppearanceStore } from '../store/appearance'
import { useUiStore } from '../store/ui'

interface Command {
  id: string
  label: string
  hint?: string
  group: string
  shortcut?: string
  icon: React.ReactNode
  run: () => void
}

/**
 * The command palette (Ctrl/Cmd+K).
 *
 * Built from live state rather than a static list, so the instances it offers are
 * the ones running *now*. A palette that lists a stale instance sends you to a page
 * that 404s, which is the fastest way to make a keyboard-first tool feel unfinished.
 *
 * The filter is a plain substring match over the label and the group, deliberately:
 * fuzzy matching helps when the labels are long prose, and these are six-character
 * iids and four-character page names.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const navigate = useNavigate()
  const emulations = useEmulations()
  const openLaunch = useUiStore((state) => state.openLaunch)
  const openSettings = useUiStore((state) => state.openSettings)
  const theme = useAppearanceStore((state) => state.theme)
  const density = useAppearanceStore((state) => state.density)
  const setTheme = useAppearanceStore((state) => state.setTheme)
  const setDensity = useAppearanceStore((state) => state.setDensity)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const commands = useMemo<Command[]>(() => {
    const go = (path: string) => () => {
      navigate(path)
      onClose()
    }
    const list: Command[] = [
      { id: 'home', label: '总览', group: '导航', shortcut: 'G 然后 H', icon: <Gauge className="h-3.5 w-3.5" />, run: go('/') },
      { id: 'instances', label: '实例记录', group: '导航', icon: <Cpu className="h-3.5 w-3.5" />, run: go('/instances') },
      { id: 'plugins', label: '插件中心', group: '导航', icon: <Boxes className="h-3.5 w-3.5" />, run: go('/plugins') },
      { id: 'work-policy', label: '工作策略', group: '导航', icon: <ListChecks className="h-3.5 w-3.5" />, run: go('/work-policy') },
      // An action, not a navigation: settings is a sheet over the current page, so
      // going there means opening it and staying exactly where you are.
      {
        id: 'settings',
        label: '设置与主题',
        hint: '外观、密度、动效与 API 令牌',
        group: '动作',
        icon: <SettingsIcon className="h-3.5 w-3.5" />,
        run: () => {
          openSettings()
          onClose()
        },
      },
      // First among the actions, because it is the one that does the work. It opens a
      // window rather than navigating: the form is not on any page any more, so a
      // navigation would have nothing to show.
      {
        id: 'launch',
        label: '新建实例',
        hint: '三种固件来源；接口等启动结束才返回',
        group: '动作',
        icon: <Plus className="h-3.5 w-3.5" />,
        run: () => {
          openLaunch()
          onClose()
        },
      },
    ]

    // Appearance, cycled rather than listed twelve times: the picker on the settings
    // page is where a theme is *chosen*; this is where a projector is *adapted to*,
    // which is two keystrokes instead of a navigation.
    const nextTheme = THEMES[(THEMES.findIndex((item) => item.id === theme) + 1) % THEMES.length]
    if (nextTheme) {
      list.push({
        id: 'cycle-theme',
        label: `切换主题（当前：${THEMES.find((item) => item.id === theme)?.name ?? theme}）`,
        hint: `下一个：${nextTheme.name}`,
        group: '外观',
        icon: <Palette className="h-3.5 w-3.5" />,
        run: () => setTheme(nextTheme.id),
      })
    }
    const nextDensity = DENSITIES[(DENSITIES.indexOf(density) + 1) % DENSITIES.length]
    if (nextDensity) {
      list.push({
        id: 'cycle-density',
        label: `切换界面密度（当前：${density}）`,
        hint: `下一个：${nextDensity}`,
        group: '外观',
        icon: <Rows3 className="h-3.5 w-3.5" />,
        run: () => setDensity(nextDensity),
      })
    }

    for (const item of emulations.data ?? []) {
      list.push({
        id: `iid-${item.iid}`,
        label: `实例 ${item.iid}`,
        hint: `${item.arch || '未知架构'} · ${item.web_ok ? 'Web 可达' : 'Web 未达'}`,
        group: '最近实例',
        icon: <TerminalIcon className="h-3.5 w-3.5" />,
        run: go(`/instances/${item.iid}`),
      })
      list.push({
        id: `term-${item.iid}`,
        label: `打开实例 ${item.iid} 的终端`,
        hint: '交互式串口',
        group: '终端',
        icon: <TerminalIcon className="h-3.5 w-3.5" />,
        run: go(`/instances/${item.iid}/terminal`),
      })
    }

    list.push({
      id: 'export',
      label: '导出运行记录 CSV',
      hint: '全库累计，浏览器直接下载',
      group: '数据',
      icon: <FileDown className="h-3.5 w-3.5" />,
      run: () => {
        setBusy('export')
        // Fetched rather than linked so a 401 is reported in the palette instead of
        // navigating the tab to a JSON error body.
        api
          .exportCsv()
          .then((blob) => {
            const url = URL.createObjectURL(blob)
            const anchor = document.createElement('a')
            anchor.href = url
            anchor.download = 'iris-runs.csv'
            anchor.click()
            URL.revokeObjectURL(url)
            setNotice(`已导出 ${blob.size} 字节`)
          })
          .catch((error: Error) => setNotice(`导出失败：${error.message}`))
          .finally(() => setBusy(null))
        onClose()
      },
    })
    return list
  }, [density, emulations.data, navigate, onClose, openLaunch, openSettings, setDensity, setTheme, theme])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return commands
    return commands.filter(
      (command) => command.label.toLowerCase().includes(needle) || command.group.toLowerCase().includes(needle),
    )
  }, [commands, query])

  useEffect(() => {
    if (!open) {
      setQuery('')
      setCursor(0)
      setNotice(null)
      return
    }
    inputRef.current?.focus()
  }, [open])

  useEffect(() => {
    setCursor((value) => Math.min(value, Math.max(0, filtered.length - 1)))
  }, [filtered.length])

  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [cursor, filtered.length])

  if (!open) return null

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setCursor((value) => Math.min(value + 1, filtered.length - 1))
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setCursor((value) => Math.max(value - 1, 0))
      return
    }
    if (event.key === 'Enter') {
      event.preventDefault()
      filtered[cursor]?.run()
    }
  }

  let lastGroup = ''

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-surface-scrim p-8 pt-[12vh] backdrop-blur-sm"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        className="glass flex max-h-[60vh] w-full max-w-xl flex-col overflow-hidden shadow-glass"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="flex items-center gap-2 border-b border-surface-border px-3 py-2">
          <Search className="h-4 w-4 shrink-0 text-ink-500" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="跳转页面、打开实例终端、导出 CSV…"
            aria-label="命令搜索"
            className="h-7 flex-1 bg-transparent text-xs text-ink-100 placeholder:text-ink-700 focus:outline-none"
          />
          <kbd className="rounded border border-surface-border px-1 font-mono text-[10px] text-ink-700">Esc</kbd>
        </div>

        <ul ref={listRef} className="scroll-y min-h-0 flex-1 py-1" role="listbox" aria-label="命令">
          {filtered.length === 0 && (
            <li className="px-3 py-4 text-center text-2xs text-ink-700">没有匹配的命令</li>
          )}
          {filtered.map((command, index) => {
            const header = command.group !== lastGroup ? command.group : null
            lastGroup = command.group
            return (
              <li key={command.id}>
                {header && (
                  <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-ink-700">
                    {header}
                  </div>
                )}
                <button
                  type="button"
                  role="option"
                  aria-selected={index === cursor}
                  data-active={index === cursor}
                  onMouseEnter={() => setCursor(index)}
                  onClick={command.run}
                  className={classNames(
                    'flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors',
                    index === cursor ? 'bg-iris-500/15 text-ink-100' : 'text-ink-300 hover:bg-surface-hover',
                  )}
                >
                  <span className="text-iris-400">{command.icon}</span>
                  <span className="flex-1 truncate">{command.label}</span>
                  {command.hint && <span className="truncate text-2xs text-ink-700">{command.hint}</span>}
                  {command.shortcut && (
                    <kbd className="shrink-0 rounded border border-surface-border px-1 font-mono text-[10px] text-ink-700">
                      {command.shortcut}
                    </kbd>
                  )}
                </button>
              </li>
            )
          })}
        </ul>

        <footer className="flex items-center gap-2 border-t border-surface-border px-3 py-1.5 text-[10px] text-ink-700">
          {busy ? (
            <Badge tone="iris">正在导出…</Badge>
          ) : notice ? (
            <span className="text-success">{notice}</span>
          ) : (
            <>
              <StatusDot tone="neutral" />
              <span>↑↓ 选择 · Enter 执行 · Esc 关闭</span>
              <span className="ml-auto">{filtered.length} 条命令</span>
            </>
          )}
        </footer>
      </div>
    </div>
  )
}