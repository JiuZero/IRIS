import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'

import { Badge, Button } from '../components/ui'
import { classNames } from '../lib/format'
import { useStats } from '../hooks/queries'
import { useUiStore } from '../store/ui'

/**
 * The bottom drawer: a live tail of the failures the corpus has actually hit.
 *
 * It is scoped to failures on purpose. A general activity log would need a server
 * event stream this project does not have, and inventing one client-side from
 * polling would show the same numbers as the cards above it. This drawer instead
 * answers a question the cards cannot: *what is going wrong right now*.
 */
export function LogPanel() {
  const open = useUiStore((state) => state.logPanelOpen)
  const setOpen = useUiStore((state) => state.setLogPanelOpen)
  const height = useUiStore((state) => state.logPanelHeight)
  const setPanelWidth = useUiStore((state) => state.setPanelWidth)
  const stats = useStats()
  const bodyRef = useRef<HTMLDivElement>(null)

  const rows = stats.data?.failures ?? []

  // Follow the tail as new failures arrive, but only when the reader is already at
  // the bottom: yanking the viewport away from someone reading an older line is
  // worse than not auto-scrolling.
  useEffect(() => {
    const body = bodyRef.current
    if (!body) return
    const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 24
    if (atBottom) body.scrollTop = body.scrollHeight
  }, [rows])

  if (!open) return null

  return (
    <section
      aria-label="失败事件抽屉"
      className="glass relative z-20 flex shrink-0 flex-col overflow-hidden rounded-none border-x-0 border-b-0"
      style={{ height }}
    >
      <header className="flex h-8 shrink-0 items-center gap-2 border-b border-surface-border px-3">
        <h2 className="text-2xs font-semibold uppercase tracking-wider text-ink-500">失败聚类</h2>
        <Badge tone="neutral">{rows.length}</Badge>
        <span className="truncate text-[10px] text-ink-700">按阶段与频次排序，不含信息类信号</span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="ml-auto rounded p-1 text-ink-500 transition-colors hover:bg-white/5 hover:text-ink-100"
          aria-label="关闭失败抽屉 (Ctrl+J)"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </header>
      <div ref={bodyRef} className="scroll-y min-h-0 flex-1 px-3 py-1.5 font-mono text-[11px] leading-relaxed">
        {rows.length === 0 && <p className="py-2 text-ink-700">尚无失败记录。</p>}
        {rows.map((row) => (
          <div key={`${row.stage}-${row.kind}`} className="flex items-center gap-2 py-0.5">
            <span className="w-16 shrink-0 text-ink-700">{row.stage || '—'}</span>
            <span
              className={classNames(
                'flex-1 truncate',
                row.kind.startsWith('link-no') ? 'text-warning' : 'text-ink-300',
              )}
            >
              {row.kind}
            </span>
            <span className="tnum shrink-0 text-ink-500">{row.count} 次</span>
          </div>
        ))}
      </div>
      <button
        type="button"
        aria-label="拖动调整抽屉高度"
        onPointerDown={(event) => {
          const originY = event.clientY
          const originH = height
          const move = (moveEvent: PointerEvent) => {
            setPanelWidth('logpanel', originH - (moveEvent.clientY - originY))
          }
          const stop = () => {
            window.removeEventListener('pointermove', move)
            window.removeEventListener('pointerup', stop)
          }
          window.addEventListener('pointermove', move)
          window.addEventListener('pointerup', stop)
        }}
        className="h-1.5 w-full shrink-0 cursor-row-resize bg-transparent transition-colors hover:bg-iris-400/40"
      />
    </section>
  )
}

export function LogPanelToggle() {
  const open = useUiStore((state) => state.logPanelOpen)
  const toggle = useUiStore((state) => state.toggleLogPanel)
  return (
    <Button size="sm" variant="ghost" onClick={toggle} aria-pressed={open} title="失败抽屉 (Ctrl+J)">
      失败抽屉
      <kbd className="rounded border border-surface-border px-1 font-mono text-[10px] text-ink-700">Ctrl J</kbd>
    </Button>
  )
}