/** Tailwind reads the design tokens out of CSS variables rather than repeating
 *  hex values, so there is exactly one place a colour is written down.
 *
 *  Why CSS variables and not the default palette: the four iris blues are the
 *  product's identity and the semantic five are how a state is read at a glance.
 *  Both are fixed by the design, and a `blue-500` that happens to be close is a
 *  colour that will drift the first time someone adjusts a shade.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        iris: {
          400: 'var(--iris-400)',
          500: 'var(--iris-500)',
          600: 'var(--iris-600)',
        },
        success: 'var(--success)',
        warning: 'var(--warning)',
        danger: 'var(--danger)',
        violet: 'var(--violet)',
        cyan: 'var(--cyan)',
        ink: {
          100: 'var(--text-primary)',
          300: 'var(--text-secondary)',
          500: 'var(--text-muted)',
          700: 'var(--text-faint)',
        },
        surface: {
          base: 'var(--surface-base)',
          raised: 'var(--surface-raised)',
          sunken: 'var(--surface-sunken)',
          border: 'var(--surface-border)',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Consolas', 'monospace'],
      },
      fontSize: {
        '2xs': ['11px', { lineHeight: '16px' }],
      },
      borderRadius: {
        card: '8px',
        panel: '12px',
      },
      backdropBlur: {
        glass: '20px',
      },
      spacing: {
        header: '56px',
        footer: '32px',
        sidebar: '264px',
        inspector: '340px',
      },
      boxShadow: {
        glass: '0 8px 32px rgba(0, 0, 0, 0.35)',
        lift: '0 6px 20px rgba(0, 0, 0, 0.28)',
        iris: '0 0 0 1px rgba(91, 141, 255, 0.35), 0 0 24px rgba(53, 99, 255, 0.18)',
      },
      transitionTimingFunction: {
        iris: 'cubic-bezier(0.22, 0.61, 0.36, 1)',
      },
      keyframes: {
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-ring': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.45' },
        },
      },
      animation: {
        'fade-up': 'fade-up 180ms cubic-bezier(0.22, 0.61, 0.36, 1) both',
        'pulse-ring': 'pulse-ring 1.8s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}