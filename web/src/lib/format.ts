/**
 * Formatting helpers.
 *
 * Grouped in one module because the rule they all follow is the same: **an absent
 * measurement renders as a dash, never as a zero.** The server sends `null` for
 * "not measured" and `0` for "measured, and it was zero", and a helper that did
 * `value ?? 0` would collapse that distinction on the exact panels where it
 * matters most -- a container that has just started, a run that died before the
 * probe answered.
 */

/** The em dash used for every absent value. */
export const DASH = '—'

export function isPresent(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** A percentage, or a dash. `digits` controls the decimal places. */
export function percent(value: number | null | undefined, digits = 1): string {
  if (!isPresent(value)) return DASH
  return `${(value * 100).toFixed(digits)}%`
}

/** A percentage that is already in percent units (`docker stats` reports 11.62). */
export function percentPoints(value: number | null | undefined, digits = 2): string {
  if (!isPresent(value)) return DASH
  return `${value.toFixed(digits)}%`
}

export function megabytes(value: number | null | undefined, digits = 0): string {
  if (!isPresent(value)) return DASH
  if (value >= 1024) return `${(value / 1024).toFixed(2)} GB`
  return `${value.toFixed(digits)} MB`
}

export function seconds(value: number | null | undefined, digits = 1): string {
  if (!isPresent(value)) return DASH
  return `${value.toFixed(digits)}s`
}

/** Bytes as `1.4 MiB`, with the same unit ladder and rounding as the server's
 *  `human_bytes`, so a number the page prints and a number the API returns do not
 *  disagree by a factor of 1024.
 *
 *  Lives here rather than in the two components that print file sizes so the two
 *  cannot drift apart: an upload card quoting `12.0 GiB` next to the picker's
 *  `12 GiB` would look like the same file measured two ways. */
export function formatBytes(count: number | null | undefined): string {
  if (!isPresent(count) || count < 0) return DASH
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = count
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1
  return `${value.toFixed(digits)} ${units[unit]}`
}

/** An ISO timestamp as local wall time; the dash when there is none. */
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return DASH
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return DASH
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

export function shortDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return DASH
  return parsed.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

/**
 * Seconds since an ISO timestamp, for "how long has this been up".
 *
 * `now` is a parameter rather than a hidden `Date.now()` so a caller can make the
 * label advance by feeding it the tick from `useTicker`. Without it the label is
 * computed once per render and only changes when something else re-renders, which
 * looks like a stuck clock on a page that is otherwise live.
 */
export function sinceLabel(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return DASH
  const started = new Date(iso).getTime()
  if (Number.isNaN(started)) return DASH
  const elapsed = Math.max(0, Math.floor((now - started) / 1000))
  return formatDuration(elapsed)
}

/** A duration in seconds, at the largest unit that still says something useful. */
export function formatDuration(total: number): string {
  if (!Number.isFinite(total) || total < 0) return DASH
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes}m ${total % 60}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

/** A `FailureKind` value as the words the taxonomy gives it. */
const FAILURE_LABELS: Record<string, string> = {
  'link-no-arp': '二层：ARP 无应答',
  'link-no-route': '二层：无路由',
  'link-no-icmp': '三层：ICMP 不通',
  'no-http-service': '四层：HTTP 未起',
  'no-guest-ip': 'guest 未获得地址',
  'boot-timeout': '启动超时',
  'qemu-exit': 'QEMU 退出',
  'web-slow': 'Web 过慢',
}

export function failureLabel(kind: string): string {
  if (!kind) return DASH
  return FAILURE_LABELS[kind] ?? kind
}

/** `iris.emulate.linkprobe.LayerState` layer names.
 *
 *  The values are lower case because `LinkLayer` is a `StrEnum` whose *values* are
 *  `"route" / "arp" / "icmp" / "service"` -- the upper-case names are Python-side
 *  member names and never reach the wire. A lookup keyed on the member names would
 *  silently fall through and render the raw English on every layer row.
 *
 *  The order is `LAYER_ORDER` from the same module: physical order, which is the
 *  order a reader thinks in. It is *not* the order the probes run in (that is
 *  route, icmp, arp, service), and showing the link diagram in execution order
 *  would put a layer-3 answer above the layer-2 answer it depends on.
 */
const LAYER_LABELS: Record<string, string> = {
  route: '路由',
  arp: 'ARP',
  icmp: 'ICMP',
  service: '服务',
}

export function layerLabel(layer: string): string {
  return LAYER_LABELS[layer.toLowerCase()] ?? layer
}

export const LAYER_ORDER = ['route', 'arp', 'icmp', 'service'] as const

/** Sort link layers into physical order, keeping any unknown layer at the end
 *  rather than dropping it -- a new layer must still be visible before it is named. */
export function sortLayers<T extends { layer: string }>(layers: readonly T[]): T[] {
  return [...layers].sort((a, b) => {
    const left = LAYER_ORDER.indexOf(a.layer.toLowerCase() as (typeof LAYER_ORDER)[number])
    const right = LAYER_ORDER.indexOf(b.layer.toLowerCase() as (typeof LAYER_ORDER)[number])
    return (left === -1 ? 99 : left) - (right === -1 ? 99 : right)
  })
}

export function classNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}