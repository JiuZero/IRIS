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

import type { ReactNode } from 'react'

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
 *  the most visible sign that a form was assembled page by page. */
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
    <label className={classNames('flex flex-col gap-1', className)}>
      {label && <span className="text-2xs font-medium text-ink-500">{label}</span>}
      {children}
      {hint && <span className="text-2xs text-ink-500">{hint}</span>}
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

export function Select({
  label,
  className,
  children,
  ...rest
}: { label?: string } & React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <FieldShell label={label} className={className}>
      <select className="field field-select" {...rest}>
        {children}
      </select>
    </FieldShell>
  )
}

/** The file picker. The visible filename is the page's own text next to a themed
 *  button, so the browser's untranslated "Choose File" -- which is also the
 *  platform's grey, and cannot be themed -- never appears. */
export function FileInput({
  label,
  hint,
  file,
  accept,
  disabled,
  onPick,
}: {
  label?: string
  hint?: ReactNode
  file: File | null
  accept?: string
  disabled?: boolean
  onPick: (file: File | null) => void
}) {
  return (
    <FieldShell label={label} hint={hint} className="min-w-56 flex-1">
      <span className="flex items-center gap-2">
        <input
          type="file"
          accept={accept}
          disabled={disabled}
          className="field field-file w-auto flex-none"
          onChange={(event) => {
            const picked = event.target.files?.[0] ?? null
            onPick(picked)
            // Reset so picking the same file twice in a row still fires `change`;
            // without this, the second selection is silently ignored.
            event.target.value = ''
          }}
        />
        {file && (
          <span className="min-w-0 truncate font-mono text-2xs text-ink-300" title={file.name}>
            {file.name}
            <span className="ml-1.5 text-ink-500">{formatBytes(file.size)}</span>
          </span>
        )}
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