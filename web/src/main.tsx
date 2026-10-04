import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'

import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@xterm/xterm/css/xterm.css'
import './tokens.css'

import { App } from './App'

/**
 * Query defaults, chosen for a dashboard that shows live state.
 *
 * `refetchInterval` is set per query rather than globally: the stat cards poll,
 * the run table does not, and a global default would either hammer the API or make
 * a live panel look frozen. `retry: 1` because a failed poll should try once more
 * and then show the error -- retrying a 401 three times just delays the message
 * that tells the user to enter a token.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 1_000,
      refetchOnWindowFocus: true,
    },
  },
})

const container = document.getElementById('root')
if (!container) {
  throw new Error('#root is missing from index.html')
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)