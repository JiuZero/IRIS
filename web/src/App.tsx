import { Navigate, Route, Routes } from 'react-router-dom'
import { TooltipProvider } from '@radix-ui/react-tooltip'

import { Shell } from './layout/Shell'
import { Dashboard } from './pages/Dashboard'
import { InstanceDetail } from './pages/InstanceDetail'
import { Instances } from './pages/Instances'
import { Settings } from './pages/Settings'
import { TerminalPage } from './pages/TerminalPage'

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