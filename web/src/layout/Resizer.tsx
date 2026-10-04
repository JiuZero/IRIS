import { useCallback } from 'react'

import { classNames } from '../lib/format'
import { useDrag } from '../hooks'

/**
 * The draggable edge between two panels.
 *
 * A `separator` role with `aria-orientation` and a real `aria-valuenow`: a resizer
 * that only responds to a mouse is unusable with a keyboard, and this is the one
 * control in the layout that a keyboard user would otherwise have no way to reach.
 * Arrow keys move it 16px, Page Up/Down by 64, Home/End to the bounds.
 */
export function Resizer({
  orientation,
  value,
  min,
  max,
  label,
  /** `1` for a right-hand panel (drag right to widen), `-1` for a left-hand one. */
  direction = 1,
  onChange,
}: {
  orientation: 'vertical' | 'horizontal'
  value: number
  min: number
  max: number
  label: string
  direction?: 1 | -1
  onChange: (value: number) => void
}) {
  const onDelta = useCallback(
    (dx: number, dy: number) => {
      const delta = orientation === 'vertical' ? dx : dy
      onChange(Math.min(max, Math.max(min, value + delta * direction)))
    },
    [direction, max, min, onChange, orientation, value],
  )
  const onPointerDown = useDrag(onDelta)

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const grow = orientation === 'vertical' ? 'ArrowRight' : 'ArrowDown'
    const shrink = orientation === 'vertical' ? 'ArrowLeft' : 'ArrowUp'
    const step = 16
    let next: number | null = null
    if (event.key === grow) next = value + step * direction
    else if (event.key === shrink) next = value - step * direction
    else if (event.key === 'PageUp') next = value + 64 * direction
    else if (event.key === 'PageDown') next = value - 64 * direction
    else if (event.key === 'Home') next = min
    else if (event.key === 'End') next = max
    if (next === null) return
    event.preventDefault()
    onChange(Math.min(max, Math.max(min, next)))
  }

  const horizontal = orientation === 'vertical'

  return (
    <div
      role="separator"
      aria-orientation={horizontal ? 'vertical' : 'horizontal'}
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      className={classNames(
        'group relative z-10 shrink-0 touch-none',
        horizontal ? 'w-px cursor-col-resize' : 'h-px cursor-row-resize',
      )}
    >
      {/* The visible line is 1px; the hit area is 9px. A 1px drag target is a
          precision task, and this is a control people adjust by feel. */}
      <span
        className={classNames(
          'absolute bg-surface-border-strong transition-colors group-hover:bg-iris-400/70 group-focus-visible:bg-iris-400',
          horizontal ? 'inset-y-0 -left-1 w-[9px]' : 'inset-x-0 -top-1 h-[9px]',
        )}
      />
    </div>
  )
}