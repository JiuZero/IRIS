/**
 * The one modal.
 *
 * Two windows in this workbench open over the page rather than on it -- launching an
 * emulation and reading one recorded run -- and they are the same shape: a scrim, a
 * titled panel, a scrolling body and a right-aligned footer. Written once, because
 * two hand-built overlays is how a product ends up with two different corner radii
 * and two different answers to "what does Esc do".
 *
 * Three behaviours are not decoration, and each is here because the alternative
 * silently breaks:
 *
 *  - **Esc closes, and the backdrop closes.** Backdrop dismissal is checked on
 *    `mousedown` with `target === currentTarget`, so a drag that *starts* on the
 *    panel and ends on the scrim does not close a window the user was using.
 *  - **Tab stays inside.** `aria-modal="true"` is a claim; without the trap it is a
 *    lie, and the browser's focus ring walks off into the page behind while the
 *    window is still open.
 *  - **Focus comes back.** Whichever control opened the window gets focus again on
 *    close, so a keyboard user is not dropped at the top of the document.
 */

import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'

import { classNames } from '../lib/format'

/** Everything a user can drive with Tab. Kept as a selector list rather than a
 *  hand-rolled walk of the DOM, because the traversal is the part that is easy to
 *  get subtly wrong and impossible to notice until someone tabs off the window. */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function Modal({
  open,
  title,
  subtitle,
  onClose,
  footer,
  children,
  width = 'max-w-2xl',
}: {
  open: boolean
  /** A string also becomes the dialog's accessible name, which is what a screen
   *  reader announces when the window opens. */
  title: ReactNode
  subtitle?: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  width?: string
}) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const panel = panelRef.current
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE)
    // The panel itself is the fallback: a window whose body is all text has nothing
    // to focus, and leaving focus on the trigger means the first Tab escapes it.
    const landing = first ?? panel
    if (landing) landing.focus()

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab' || !panelRef.current) return
      const stops = [...panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
      const first = stops[0]
      const last = stops[stops.length - 1]
      if (!first || !last) return
      // Only the two edges are handled. Wrapping the *whole* traversal by hand is
      // what libraries do, and re-implementing it half-correctly traps focus.
      const edge = event.shiftKey ? first : last
      if (document.activeElement !== edge) return
      event.preventDefault()
      ;(event.shiftKey ? last : first).focus()
    }

    // Capture phase: the terminal page and the hotkey layer both listen on
    // `window`, and a document listener in the bubble phase can lose the race to a
    // shortcut that has already called `preventDefault`.
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      opener?.focus()
    }
  }, [onClose, open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-surface-scrim p-4 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
      role="presentation"
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        tabIndex={-1}
        className={classNames(
          'glass my-4 flex max-h-[calc(100vh-2rem)] w-full flex-col overflow-hidden shadow-glass outline-none',
          width,
        )}
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-surface-border px-5 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-ink-100">{title}</h2>
            {subtitle && <p className="mt-0.5 text-2xs leading-relaxed text-ink-500">{subtitle}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded p-1 text-ink-500 transition-colors hover:bg-surface-hover hover:text-ink-100"
            title="关闭"
            aria-label="关闭"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>

        <div className="scroll-y min-h-0 flex-1 p-5">{children}</div>

        {footer && (
          <footer className="flex shrink-0 flex-wrap items-center gap-2 border-t border-surface-border px-5 py-3">
            {footer}
          </footer>
        )}
      </div>
    </div>
  )
}