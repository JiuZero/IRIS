import { useLayoutEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { TooltipProvider } from '@radix-ui/react-tooltip'

import { Shell } from './layout/Shell'
import { Dashboard } from './pages/Dashboard'
import { InstanceDetail } from './pages/InstanceDetail'
import { Instances } from './pages/Instances'
import { Settings } from './pages/Settings'
import { TerminalPage } from './pages/TerminalPage'
import { applyAppearance, useAppearanceStore } from './store/appearance'

/**
 * The routes.
 *
 * Five screens, and the terminal is a *separate* route from the instance detail
 * rather than a tab inside it. That is the one place this layout departs from a
 * plain tabbed page, and the reason is the shortcut: Ctrl/Cmd+` has to land on a
 * full-width terminal from anywhere, including from another instance's page. A tab
 * would have to remember which tab was open.
 *
 * `/instances/:id/*` covers the three sub-tabs, so a link to a tab is a real URL
 * and the browser's back button steps out of a tab instead of off the instance.
 */
export function App() {
  const theme = useAppearanceStore((state) => state.theme)
  const density = useAppearanceStore((state) => state.density)
  const font = useAppearanceStore((state) => state.font)
  const animations = useAppearanceStore((state) => state.animations)

  // The four attributes on <html> are what the stylesheet themes off; the store is
  // the only place they are decided. Mounting here rather than in a provider means
  // the effect runs once for the document rather than once per consumer.
  //
  // A layout effect, deliberately: React runs every layout effect before any
  // passive one, bottom-up, so this writes `data-theme` before a child's passive
  // effect reads the resolved colours. The terminal page does exactly that --
  // xterm is repainted from `getComputedStyle`, and as a passive effect it would
  // otherwise read the *previous* theme's values and repaint with them.
  useLayoutEffect(() => {
    applyAppearance({ theme, density, font, animations })
  }, [theme, density, font, animations])

  return (
    <TooltipProvider delayDuration={300} skipDelayDuration={200}>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Dashboard />} />
          <Route path="instances" element={<Instances />} />
          <Route path="instances/:iid" element={<InstanceDetail />} />
          <Route path="instances/:iid/terminal" element={<TerminalPage />} />
          <Route path="settings" element={<Settings />} />
          {/* A typo in a path should land on the dashboard, not on a blank page with
              no way back except the browser button. */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </TooltipProvider>
  )
}