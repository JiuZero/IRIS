import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `base: '/'` rather than './'. A relative base looks like it would survive being
// mounted under a sub-path, but it cannot be combined with the SPA fallback:
// `/instances/7100` is served the same index.html as `/`, and from that page a
// relative `./assets/app.js` resolves to `/instances/assets/app.js`, which does
// not exist. The workbench is served from the root by `iris web`, so absolute is
// both correct and the only option that survives a deep link.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: 'dist',
    // Source maps ship with the bundle on purpose: a demo that breaks on the
    // reviewer's machine is debuggable only if the stack traces are readable.
    sourcemap: true,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // Split the one heavy vendor out of the app chunk: the terminal is ~300 KB
        // and is not needed to paint the first screen. Only packages that something
        // actually imports may be listed -- an entry with no importer produces an
        // empty chunk that index.html still preloads.
        manualChunks: {
          xterm: ['@xterm/xterm', '@xterm/addon-fit'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // `npm run dev` talks to a separately running `iris web`, which serves the
      // API but not the pages. Proxying keeps the dev server same-origin, so no
      // code has to know it is running under two different origins.
      '/api': { target: 'http://127.0.0.1:9000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:9000', ws: true },
    },
  },
})