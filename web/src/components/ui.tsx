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

import { classNames, DASH } from '../lib/format'

/* ------------------------------------------------------------------ surfaces */

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={classNames('glass flex min-h-0 flex-col', className)}>
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

/* ---------------------------------------------------------------- stat tiles */

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
    // The graph-paper wash is what makes an empty panel read as "nothing here
    // yet" rather than "this panel failed to draw"; the lines are one to three
    // percent opacity, so the text over them is unaffected.
    <div className="panel-grid flex h-full min-h-32 flex-col items-center justify-center gap-2 p-6 text-center">
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

export function TextInput({
  label,
  hint,
  ...rest
}: { label?: string; hint?: ReactNode } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="flex flex-col gap-1">
      {label && <span className="text-2xs font-medium text-ink-500">{label}</span>}
      <input
        className="h-8 rounded-card border border-surface-border bg-surface-input px-2 text-xs text-ink-100 placeholder:text-ink-700 focus:border-iris-400 focus:outline-none"
        {...rest}
      />
      {hint && <span className="text-2xs text-ink-500">{hint}</span>}
    </label>
  )
}

export function Select({
  label,
  children,
  ...rest
}: { label?: string } & React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <label className="flex flex-col gap-1">
      {label && <span className="text-2xs font-medium text-ink-500">{label}</span>}
      <select
        className="h-8 rounded-card border border-surface-border bg-surface-input px-2 text-xs text-ink-100 focus:border-iris-400 focus:outline-none"
        {...rest}
      >
        {children}
      </select>
    </label>
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