import { useCallback, useEffect, useState } from 'react'

import { breakpointOf, type Breakpoint } from '../store/ui'

/**
 * The viewport band, tracked in one place.
 *
 * `matchMedia` rather than a resize listener plus arithmetic: the bands are
 * expressed in the same CSS the layout uses, and the browser already knows how to
 * answer this without a layout read on every event. The listener is the modern
 * `addEventListener` form -- the deprecated `addListener` is absent in Safari 14+.
 */
export function useBreakpoint(): Breakpoint {
  const [band, setBand] = useState<Breakpoint>(() =>
    typeof window === 'undefined' ? 'wide' : breakpointOf(window.innerWidth),
  )

  useEffect(() => {
    const queries = [
      { band: 'wide' as const, query: '(min-width: 1440px)' },
      { band: 'desktop' as const, query: '(min-width: 1200px)' },
      { band: 'laptop' as const, query: '(min-width: 1024px)' },
    ]
    const cleanups = queries.map(({ band: next, query }) => {
      const mql = window.matchMedia(query)
      const handler = () => setBand(next)
      mql.addEventListener('change', handler)
      return () => mql.removeEventListener('change', handler)
    })
    // Below 1024 no query matches, which is exactly the signal for the narrow band.
    // Handled by a direct width read rather than a fourth query, because "not wide,
    // not desktop, not laptop" is not something matchMedia can express.
    const onResize = () => {
      const measured = breakpointOf(window.innerWidth)
      setBand(queries.find(({ query }) => window.matchMedia(query).matches)?.band ?? measured)
    }
    window.addEventListener('resize', onResize)
    onResize()
    return () => {
      cleanups.forEach((cleanup) => cleanup())
      window.removeEventListener('resize', onResize)
    }
  }, [])

  return band
}

/** True once the user has asked for less motion. Read live rather than latched at
 *  mount, because the preference can change while the tab is open. */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )
  useEffect(() => {
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)')
    const handler = () => setReduced(mql.matches)
    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [])
  return reduced
}

/** A value that ticks, for "up 12m 30s" labels. Paused when the tab is hidden so a
 *  background tab is not waking the CPU once a second to increment a counter no
 *  one is looking at. */
export function useTicker(intervalMs = 1_000, active = true): number {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => {
      if (!document.hidden) setTick((value) => value + 1)
    }, intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs, active])
  return tick
}

/** Run `handler` on a pointer drag, reporting a delta in px.
 *
 * Pointer capture rather than window listeners: capture keeps the drag alive when
 * the cursor leaves the element, which is the normal case for a panel edge -- the
 * user overshoots. A window listener stops firing the moment the pointer crosses
 * the boundary and the edge sticks.
 */
export function useDrag(onDelta: (deltaX: number, deltaY: number) => void, enabled = true) {
  const start = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (!enabled) return
    event.preventDefault()
    const originX = event.clientX
    const originY = event.clientY
    const target = event.currentTarget
    target.setPointerCapture(event.pointerId)

    const move = (moveEvent: PointerEvent) => {
      onDelta(moveEvent.clientX - originX, moveEvent.clientY - originY)
    }
    const stop = () => {
      target.releasePointerCapture(event.pointerId)
      target.removeEventListener('pointermove', move)
      target.removeEventListener('pointerup', stop)
      target.removeEventListener('pointercancel', stop)
    }
    target.addEventListener('pointermove', move)
    target.addEventListener('pointerup', stop)
    target.addEventListener('pointercancel', stop)
  }, [enabled, onDelta])

  return start
}