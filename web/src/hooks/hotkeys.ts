import { useEffect } from 'react'

/** One global shortcut table.
 *
 * Bindings are declared as data so the header can *show* them next to each command
 * -- a palette entry whose shortcut is a guess is worse than one without a
 * shortcut. Handlers are kept in a ref rather than re-subscribed on every render:
 * a page that polls every three seconds would otherwise tear down and rebuild the
 * listener eight times a minute, and a key pressed in that window would be dropped.
 */
export interface Hotkey {
  /** `mod` is Cmd on macOS and Ctrl everywhere else -- the convention for
   *  "the command key", and the one users already have in their fingers. */
  combo: string
  handler: (event: KeyboardEvent) => void
  description: string
  /** Allow the shortcut while a text field has focus. Off for anything that moves
   *  focus or types, because stealing a keystroke from an input is unforgivable. */
  allowInInput?: boolean
}

function isTextTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
}

export function matchesCombo(event: KeyboardEvent, combo: string): boolean {
  const parts = combo.toLowerCase().split('+')
  const key = parts[parts.length - 1]
  const wantMod = parts.includes('mod')
  const wantShift = parts.includes('shift')
  const wantAlt = parts.includes('alt')

  const mod = event.metaKey || event.ctrlKey
  if (wantMod !== mod) return false
  if (wantShift !== event.shiftKey) return false
  if (wantAlt !== event.altKey) return false

  // `event.key` is layout-dependent ("z" on QWERTY), `event.code` is physical.
  // The palette is labelled with letters, so the letter has to be the one compared;
  // `code` would make Ctrl+K stop working on AZERTY.
  return event.key.toLowerCase() === key
}

export function useHotkeys(hotkeys: readonly Hotkey[], enabled = true): void {
  useEffect(() => {
    if (!enabled) return
    const onKeyDown = (event: KeyboardEvent) => {
      for (const hotkey of hotkeys) {
        if (!matchesCombo(event, hotkey.combo)) continue
        if (!hotkey.allowInInput && isTextTarget(event.target)) continue
        event.preventDefault()
        hotkey.handler(event)
        return
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [enabled, hotkeys])
}