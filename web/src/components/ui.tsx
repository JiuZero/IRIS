/**
 * The visual vocabulary: glass panels, buttons, badges, stat tiles, status dots.
 *
 * These exist as components rather than as utility strings in each page because
 * the whole product reads as one system. Three rules are enforced here once
 * instead of being re-decided per screen:
 *
 *  - **A state is never colour alone.** Every badge and dot carries a word or an
 *    icon next to the colour, because the person reading the screen may be
 *    colour-blind, on a projector, or in a screenshot printed in black and white.
 *  - **An absent measurement is a dash, not a zero.** Handled by the `format`
 *    helpers and applied here so no page has to remember.
 *  - **Icons are one of four sizes** (12/14/16/20px), which is what keeps a header
 *    row and a table row looking like the same product.
 */

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from 'lucide-react'

import { classNames, DASH, formatBytes, percentPoints } from '../lib/format'

/* ------------------------------------------------------------------ surfaces */

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
  id,
}: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  /** An anchor target, for in-page links like the sidebar's "新建实例". On the
   *  `<section>` rather than inside the body so the scroll lands on the panel's top
   *  border instead of a few pixels below its header. */
  id?: string
}) {
  return (
    <section id={id} className={classNames('glass flex min-h-0 flex-col', className)}>
      {(title || actions) && (
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-surface-border px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="truncate text-sm font-semibold text-ink-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 truncate text-2xs text-ink-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={classNames('min-h-0 flex-1', bodyClassName ?? 'p-4')}>{children}</div>
    </section>
  )
}

/* ------------------------------------------------------------------- buttons */

type ButtonVariant = 'primary' | 'ghost' | 'outline' | 'danger'

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-iris-500 text-white hover:bg-iris-400 disabled:hover:bg-iris-500 shadow-iris disabled:shadow-none',
  ghost: 'text-ink-300 hover:bg-surface-hover hover:text-ink-100',
  outline: 'border border-surface-border-strong text-ink-300 hover:border-iris-400 hover:text-ink-100',
  danger: 'border border-danger/40 text-danger hover:bg-danger/10 hover:border-danger',
}

export function Button({
  variant = 'outline',
  size = 'md',
  icon,
  children,
  className,
  ...rest
}: {
  variant?: ButtonVariant
  size?: 'sm' | 'md'
  icon?: ReactNode
  children?: ReactNode
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={classNames(
        'inline-flex items-center justify-center gap-1.5 rounded-card font-medium transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-45',
        size === 'sm' ? 'h-7 px-2 text-2xs' : 'h-8 px-3 text-xs',
        BUTTON_VARIANTS[variant],
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
    </button>
  )
}

/* -------------------------------------------------------------------- badges */

export type Tone = 'iris' | 'success' | 'warning' | 'danger' | 'violet' | 'cyan' | 'neutral'

const TONE_CLASSES: Record<Tone, string> = {
  iris: 'border-iris-400/35 bg-iris-500/12 text-iris-400',
  success: 'border-success/35 bg-success/12 text-success',
  warning: 'border-warning/35 bg-warning/12 text-warning',
  danger: 'border-danger/35 bg-danger/12 text-danger',
  violet: 'border-violet/35 bg-violet/12 text-violet',
  cyan: 'border-cyan/35 bg-cyan/12 text-cyan',
  neutral: 'border-surface-border-strong bg-surface-hover text-ink-300',
}

/** The one badge. `tone` picks the colour; the caller always supplies a word. */
export function Badge({
  tone = 'neutral',
  icon,
  children,
  title,
  className,
}: {
  tone?: Tone
  icon?: ReactNode
  children: ReactNode
  title?: string
  className?: string
}) {
  return (
    <span
      title={title}
      className={classNames(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-2xs font-medium',
        TONE_CLASSES[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  )
}

/* ---------------------------------------------------------------- status dot */

/** A pulsing dot for "live", a steady one for "idle". The pulse is a CSS animation
 *  rather than a JS timer, so the OS's `prefers-reduced-motion` rule and the
 *  in-app motion switch can both drop it. */
export function StatusDot({ tone = 'success', pulse = false }: { tone?: Tone; pulse?: boolean }) {
  const colour: Record<Tone, string> = {
    iris: 'bg-iris-400',
    success: 'bg-success',
    warning: 'bg-warning',
    danger: 'bg-danger',
    violet: 'bg-violet',
    cyan: 'bg-cyan',
    neutral: 'bg-ink-500',
  }
  // The halo reads its colour from `currentColor`, so the text utility is not
  // decoration: it is what tells the ring which state it is ringing for.
  const textColour: Record<Tone, string> = {
    iris: 'text-iris-400',
    success: 'text-success',
    warning: 'text-warning',
    danger: 'text-danger',
    violet: 'text-violet',
    cyan: 'text-cyan',
    neutral: 'text-ink-500',
  }
  return (
    <span
      aria-hidden="true"
      className={classNames(
        'inline-block h-1.5 w-1.5 shrink-0 rounded-full',
        colour[tone],
        textColour[tone],
        pulse && 'status-pulse',
      )}
    />
  )
}

/* ------------------------------------------------------------------- meters */

/**
 * A labelled proportion bar, for "how full is it" readings.
 *
 * `value` is a fraction in `[0, 1]` or `null`. **`null` draws no fill and no
 * width at all** rather than an empty track that reads as 0%: the host's CPU share
 * is genuinely unmeasured on a sampler's first read, and a full-width empty bar
 * would claim the reading is zero. The caller pairs this with `isPresent` output
 * so the number beside it is a dash in the same breath.
 */
export function Meter({
  label,
  value,
  caption,
  tone = 'iris',
  size = 'md',
}: {
  label: string
  value: number | null | undefined
  /** Shown after the number; the unit, or the window the reading covers. */
  caption?: ReactNode
  tone?: Tone
  size?: 'sm' | 'md'
}) {
  const measured = typeof value === 'number' && Number.isFinite(value)
  const ratio = measured ? Math.min(1, Math.max(0, value)) : 0
  const fill: Record<Tone, string> = {
    iris: 'bg-iris-400',
    success: 'bg-success',
    warning: 'bg-warning',
    danger: 'bg-danger',
    violet: 'bg-violet',
    cyan: 'bg-cyan',
    neutral: 'bg-ink-500',
  }
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-2xs font-medium text-ink-500">{label}</span>
        <span className="tnum shrink-0 text-2xs text-ink-300">
          {measured ? percentPoints(ratio * 100, 1) : DASH}
          {caption && <span className="ml-1 text-ink-700">{caption}</span>}
        </span>
      </div>
      <div
        className={classNames(
          'overflow-hidden rounded-full bg-surface-hover',
          size === 'sm' ? 'h-1' : 'h-1.5',
        )}
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        {...(measured ? { 'aria-valuenow': Math.round(ratio * 100) } : {})}
        aria-valuetext={measured ? percentPoints(ratio * 100, 1) : '未测量'}
      >
        {measured && (
          <span
            className={classNames('block h-full rounded-full', fill[tone])}
            style={{ width: `${Math.round(ratio * 100)}%` }}
          />
        )}
      </div>
    </div>
  )
}

/* -------------------------------------------------------------- stat tiles */

export function StatTile({
  label,
  value,
  unit,
  hint,
  tone = 'neutral',
  trend,
  icon,
}: {
  label: string
  /** Already formatted. Pass the dash through for an absent measurement. */
  value: string
  unit?: string
  hint?: ReactNode
  tone?: Tone
  trend?: ReactNode
  icon?: ReactNode
}) {
  const valueTone: Record<Tone, string> = {
    iris: 'text-iris-400',
    success: 'text-success',
    warning: 'text-warning',
    danger: 'text-danger',
    violet: 'text-violet',
    cyan: 'text-cyan',
    neutral: 'text-ink-100',
  }
  return (
    <div className="glass lift flex flex-col gap-1 p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-2xs font-medium uppercase tracking-wider text-ink-500">{label}</span>
        {icon}
      </div>
      <div className="flex items-baseline gap-1">
        <span className={classNames('tnum text-2xl font-semibold leading-none', valueTone[tone])}>{value}</span>
        {unit && <span className="text-xs text-ink-500">{unit}</span>}
      </div>
      {trend}
      {hint && <p className="text-2xs leading-relaxed text-ink-500">{hint}</p>}
    </div>
  )
}

/** A labelled row for the inspector's read-only panels. */
export function DataRow({ label, children, mono = false }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-surface-border py-1.5 last:border-0">
      <span className="shrink-0 text-2xs text-ink-500">{label}</span>
      <span className={classNames('min-w-0 truncate text-right text-xs text-ink-100', mono && 'font-mono tnum')}>
        {children}
      </span>
    </div>
  )
}

/* -------------------------------------------------------------------- states */

export function EmptyState({ title, detail, action }: { title: string; detail?: string; action?: ReactNode }) {
  return (
    // A sunken well with a dashed rim, rather than the graph paper this used to
    // paint: "nothing here yet" is carried by the recess and the rim, so the
    // empty panel reads as part of the surface instead of as a hatched overlay
    // sitting on top of it.
    <div className="m-3 flex h-full min-h-32 flex-col items-center justify-center gap-2 rounded-card border border-dashed border-surface-border-strong bg-surface-faint p-6 text-center">
      <p className="text-xs font-medium text-ink-300">{title}</p>
      {detail && <p className="max-w-md text-2xs leading-relaxed text-ink-500">{detail}</p>}
      {action}
    </div>
  )
}

export function ErrorState({
  title = '请求失败',
  detail,
  onRetry,
  hint,
}: {
  title?: string
  detail?: string
  onRetry?: () => void
  hint?: ReactNode
}) {
  return (
    <div className="flex h-full min-h-32 flex-col items-center justify-center gap-2 p-6 text-center">
      <Badge tone="danger">错误</Badge>
      <p className="text-xs font-medium text-ink-100">{title}</p>
      {detail && <p className="max-w-lg break-words text-2xs leading-relaxed text-ink-500">{detail}</p>}
      {hint}
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  )
}

/** Shown wherever a number would otherwise be, while the request is in flight.
 *  A skeleton rather than a spinner: a spinner in a tile that is about to hold a
 *  percentage makes the tile look like it is measuring something. */
export function Skeleton({ className }: { className?: string }) {
  return <div className={classNames('animate-pulse rounded-card bg-surface-faint', className)} aria-hidden="true" />
}

/* -------------------------------------------------------------------- inputs */

/** Every editable field, one shell. The field itself is `.field` in `tokens.css`
 *  -- radius, border, focus ring and the number-spinner removal all live there,
 *  because a search box that is a slightly different shape from the port box is
 *  the most visible sign that a form was assembled page by page.
 *
 *  `.field-cell` is what puts a row of these on one baseline: the label, the
 *  control and the hint are the three tracks of the parent `.field-row`, and every
 *  field spans all three whether or not it has a hint. */
function FieldShell({
  label,
  hint,
  className,
  children,
}: {
  label?: ReactNode
  hint?: ReactNode
  className?: string
  children: ReactNode
}) {
  return (
    <label className={classNames('field-cell', className)}>
      {label && <span className="text-2xs font-medium text-ink-500">{label}</span>}
      {children}
      {hint && <span className="text-2xs leading-relaxed text-ink-500">{hint}</span>}
    </label>
  )
}

export function TextInput({
  label,
  hint,
  className,
  ...rest
}: { label?: string; hint?: ReactNode } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <FieldShell label={label} hint={hint} className={className}>
      <input className="field" {...rest} />
    </FieldShell>
  )
}

export interface SelectOption {
  value: string
  label: string
  /** Shown after the label, right-aligned and dimmer. Where a value is a code that
   *  has a readable name -- `link-no-arp` beside 「二层：ARP 无应答」-- this is the
   *  code, because the word alone loses what the API will hand back. */
  hint?: string
  /** A prompt rather than a value: "选择一个已提取的固件" is what the field says when
   *  nothing is chosen, and it has to read differently from a chosen arch, or the
   *  form looks like it is already answered. */
  placeholder?: boolean
  disabled?: boolean
}

/**
 * The dropdown, drawn by the page.
 *
 * A native `<select>` cannot be finished: `appearance: none` and a drawn arrow make
 * the closed box match the theme, but the *expanded* list is drawn by the operating
 * system -- OS palette, OS font, OS row metrics, OS highlight -- and no stylesheet
 * reaches it. The screenshot that prompted this was exactly that: a themed box with
 * a stock list under it. So the list is page DOM, portalled and positioned from the
 * trigger's rectangle, and what remains of the native element is nothing.
 *
 * What the native element *was* is worth keeping, so this is built to its
 * behaviour rather than to its look: the list opens on click, Enter, Space or
 * ArrowDown, ArrowUp/Home/End/PageUp/PageDown move the highlight, Enter and Space
 * commit, Escape closes without committing, Tab closes and lets focus continue
 * onward, and the arrow keys wrap. The highlight follows the pointer as well, so
 * nothing has to be learned twice. Focus never leaves the trigger -- the active
 * option is named with `aria-activedescendant` -- which is what keeps a keyboard
 * user from being dropped at the top of the window when the list closes.
 *
 * `options` is a list of `{value, label}` rather than `<option>` children on
 * purpose: reading a value out of `event.target.value` works for one control and
 * not for the next thing this needs (a per-option hint, a disabled option), and a
 * caller that builds an array can filter it before it reaches the DOM.
 */
export function Select({
  label,
  hint,
  value,
  options,
  onChange,
  disabled,
  className,
  menuClassName,
}: {
  label?: string
  /** The field's own third line, so a select takes up the same three tracks as the
   *  inputs beside it. Without it the row's hint line is short by one field. */
  hint?: ReactNode
  value: string
  options: SelectOption[]
  onChange: (value: string) => void
  disabled?: boolean
  className?: string
  menuClassName?: string
}) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [box, setBox] = useState<{ top: number; left: number; width: number; drop: 'down' | 'up' } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const listId = useId()

  const selected = options.find((option) => option.value === value)
  const selectable = options.filter((option) => !option.disabled)

  /** Anchored to the trigger rather than laid out in flow: an in-flow list is
   *  clipped by the nearest scrolling ancestor, and every caller here sits inside
   *  one -- the launch window's body, the record table's modal. Flipped upward when
   *  there is not enough room below, because a field near the bottom of a window
   *  must not open a list off the bottom of the screen. */
  const place = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const height = menuRef.current?.scrollHeight ?? 0
    const below = window.innerHeight - rect.bottom - 8
    const drop = height > below && rect.top > below ? 'up' : 'down'
    setBox({
      top: drop === 'down' ? rect.bottom + 4 : Math.max(8, rect.top - 4 - height),
      left: rect.left,
      width: rect.width,
      drop,
    })
  }, [])

  useEffect(() => {
    if (!open) return
    setActive(Math.max(0, options.findIndex((option) => option.value === value)))
    place()
    // Re-placed on any scroll, not just the window's: the list is portalled to the
    // body, so the trigger can move under a scrolling modal without the page
    // scrolling at all.
    const reposition = () => place()
    window.addEventListener('scroll', reposition, true)
    window.addEventListener('resize', reposition)
    return () => {
      window.removeEventListener('scroll', reposition, true)
      window.removeEventListener('resize', reposition)
    }
  }, [open, options, place, value])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Node && (menuRef.current?.contains(target) || triggerRef.current?.contains(target))) return
      setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    return () => document.removeEventListener('pointerdown', onPointerDown, true)
  }, [open])

  // The highlighted option is the one on screen, even when the arrow keys moved it.
  useEffect(() => {
    if (active < 0) return
    menuRef.current?.querySelector(`#${CSS.escape(`${listId}-${active}`)}`)?.scrollIntoView({ block: 'nearest' })
  }, [active, listId])

  const close = (commit: boolean) => {
    if (commit && active >= 0) {
      const option = options[active]
      if (option && !option.disabled && option.value !== value) onChange(option.value)
    }
    setOpen(false)
    // Escape and outside-click both leave the trigger holding focus, so the next
    // Tab continues from this field instead of from the top of the window.
    triggerRef.current?.focus()
  }

  /** Arrow keys wrap, the way a native list does: from the last option, Up lands on
   *  the last rather than standing still, which is how you get somewhere when the
   *  cursor is below where you meant to be. Disabled options are stepped over. */
  const move = (step: number) => {
    if (selectable.length === 0) return
    setActive((current) => {
      const from = current < 0 ? (step > 0 ? -1 : 0) : current
      let next = from
      for (let index = 0; index < options.length; index += 1) {
        next = (next + step + options.length) % options.length
        if (!options[next]?.disabled) return next
      }
      return current
    })
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (disabled) return
    if (event.key === 'Escape') {
      if (!open) return
      event.preventDefault()
      // Stopped here rather than left to bubble: this field is inside the launch
      // window, whose own Escape closes the window. Choosing an option and closing
      // the window are not the same answer.
      event.stopPropagation()
      close(false)
      return
    }
    if (!open && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault()
      setOpen(true)
      return
    }
    if (!open) return
    if (event.key === 'Tab') {
      close(false)
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      move(1)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      move(-1)
      return
    }
    if (event.key === 'Home' || event.key === 'PageUp') {
      event.preventDefault()
      setActive(selectable[0] === undefined ? -1 : options.indexOf(selectable[0]))
      return
    }
    if (event.key === 'End' || event.key === 'PageDown') {
      event.preventDefault()
      const last = selectable[selectable.length - 1]
      setActive(last === undefined ? -1 : options.indexOf(last))
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      close(true)
    }
  }

  return (
    <FieldShell label={label} hint={hint} className={className}>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        aria-label={typeof label === 'string' ? label : undefined}
        data-open={open}
        // The closed box still answers with a click on the field itself, not just on
        // the chevron, which is the affordance a native select trains people on.
        onClick={() => setOpen((state) => !state)}
        onKeyDown={onKeyDown}
        title={selected?.label}
        className="field select-trigger"
      >
        <span className="select-value" data-empty={selected?.placeholder || selected === undefined}>
          {selected?.label ?? '—'}
        </span>
        <ChevronDown
          className={classNames('h-3.5 w-3.5 transition-transform duration-150', open && 'rotate-180')}
          aria-hidden="true"
        />
      </button>

      {open &&
        box &&
        createPortal(
          <div
            ref={menuRef}
            id={listId}
            role="listbox"
            aria-label={typeof label === 'string' ? label : '选项'}
            style={{
              position: 'fixed',
              top: box.top,
              left: box.left,
              // Wide enough for its own longest option, never narrower than the
              // field it belongs to, never wider than the window: a five-character
              // field with a nine-character option would otherwise open a list that
              // hides its own label.
              width: 'max-content',
              minWidth: box.width,
              maxWidth: `${Math.max(0, window.innerWidth - box.left - 8)}px`,
              ...(box.drop === 'up' ? { transform: 'translateY(-100%)' } : null),
            }}
            className={classNames('select-menu shadow-glass', menuClassName)}
          >
            {options.map((option, index) => (
              <div
                key={option.value}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={option.value === value}
                aria-disabled={option.disabled || undefined}
                data-active={index === active}
                className="select-option"
                onMouseEnter={() => setActive(index)}
                // `pointerdown` with a `preventDefault`, so the click never lands on
                // whatever is behind the list -- on a modal scrim that would be an
                // unintended dismissal of the whole window.
                onPointerDown={(event) => {
                  if (option.disabled) return
                  event.preventDefault()
                  setActive(index)
                  setOpen(false)
                  triggerRef.current?.focus()
                  if (option.value !== value) onChange(option.value)
                }}
              >
                <Check className="select-tick h-3 w-3 shrink-0" style={{ visibility: 'hidden' }} aria-hidden="true" />
                <span className="select-option-label" title={option.label}>
                  {option.label}
                </span>
                {option.hint && <span className="shrink-0 font-mono text-ink-700">{option.hint}</span>}
              </div>
            ))}
          </div>,
          document.body,
        )}
    </FieldShell>
  )
}

/** The file picker.
 *
 *  Two elements, one control: the native input stays stretched over the drawn one,
 * transparent, because it is what a click lands on and what the browser hands the
 * picked file to. Everything visible is drawn, which is what lets the filename sit
 * *inside* the field next to the button.
 *
 *  The obvious alternative -- keeping the native control and restyling
 *  `::file-selector-button` -- cannot work here, and the reason is worth recording:
 *  a file input clears its own visible text the moment `value` is reset, so the
 *  control falls back to the browser's untranslated "No file chosen" while the page
 *  renders the name beside it. That is the "filename trailing the picker" layout,
 *  and it is unavoidable in that design rather than a styling mistake. Resetting
 *  `value` is not optional either: without it, picking the same file twice in a row
 *  fires no `change` and the second selection is silently ignored. */
export function FileInput({
  label,
  hint,
  className,
  file,
  accept,
  disabled,
  onPick,
}: {
  label?: string
  hint?: ReactNode
  className?: string
  file: File | null
  accept?: string
  disabled?: boolean
  onPick: (file: File | null) => void
}) {
  return (
    <FieldShell label={label} hint={hint} className={className}>
      <span className="relative block">
        <input
          type="file"
          accept={accept}
          disabled={disabled}
          className="field-file-native"
          onChange={(event) => {
            const picked = event.target.files?.[0] ?? null
            onPick(picked)
            event.target.value = ''
          }}
        />
        <span className={classNames('field-file', disabled && 'opacity-45')} aria-hidden="true">
          <span className="field-file-button">选择文件</span>
          <span className="min-w-0 truncate font-mono text-2xs" title={file?.name}>
            {file ? (
              <>
                <span className="text-ink-300">{file.name}</span>
                <span className="ml-1.5 text-ink-500">{formatBytes(file.size)}</span>
              </>
            ) : (
              <span className="text-ink-700">未选择文件</span>
            )}
          </span>
        </span>
      </span>
    </FieldShell>
  )
}

/** A row of mutually exclusive choices that reads as one control. `aria-pressed`
 *  on a plain button rather than a radio group, because the launch panel's three
 *  sources each change how many inputs exist, and a radio group does not read
 *  correctly when picking one of them swaps the form underneath it. */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label?: string
  value: T
  options: { value: T; label: string; hint?: string }[]
  onChange: (value: T) => void
}) {
  return (
    <div className="flex flex-col gap-1">
      {label && <span className="text-2xs font-medium text-ink-500">{label}</span>}
      <div className="segment-group" role="group" aria-label={typeof label === 'string' ? label : undefined}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className="segment flex items-center gap-1.5"
            aria-pressed={value === option.value}
            title={option.hint}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}


/** One key/value cell for the small comparison tables. */
export function InlineStat({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: Tone }) {
  const toneClass: Record<Tone, string> = {
    iris: 'text-iris-400',
    success: 'text-success',
    warning: 'text-warning',
    danger: 'text-danger',
    violet: 'text-violet',
    cyan: 'text-cyan',
    neutral: 'text-ink-100',
  }
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-2xs text-ink-500">{label}</span>
      <span className={classNames('tnum text-xs font-medium', toneClass[tone])}>{value || DASH}</span>
    </div>
  )
}