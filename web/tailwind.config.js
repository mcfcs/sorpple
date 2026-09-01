/**
 * Design tokens for the Sorpple dashboard.
 *
 * The subject is a monitoring instrument watching three feeds, so the visual
 * language is an instrument panel: a deep slate ground, warm paper-white text,
 * and one fixed signal hue per source so a listing's origin is readable before
 * you consciously read anything.
 *
 * Source hues are assigned by feel, not by brand: Prosple is the free GraphQL
 * API (cyan, the calm one), JobStreet the free HTML feed (violet), Indeed the
 * expensive proxied one (coral — it should catch your eye when it misbehaves).
 */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Ground — a blue-leaning slate, several steps apart so panels separate
        // without borders doing all the work.
        ink: {
          900: '#0b1119',   // page
          800: '#111a26',   // panel
          700: '#182432',   // raised / hover
          600: '#22303f',   // hairline
          500: '#33465a',   // disabled text
        },
        paper: {
          DEFAULT: '#e9eef5', // primary text — warm white, not pure
          dim: '#94a6bb',     // secondary
          faint: '#5f7386',   // tertiary / closed listings
        },
        // Per-source signal hues.
        prosple:   '#4fd6d2',
        jobstreet: '#a68bff',
        indeed:    '#ff8a6b',
        // Status.
        live:    '#5ee6a8',   // running / open
        soon:    '#ffc46b',   // closing soon
        halt:    '#ff6b81',   // error / paused
      },
      fontFamily: {
        // Display carries the personality; body stays out of the way; mono is
        // for every number that ticks, so nothing reflows as it counts down.
        display: ['"Space Grotesk"', 'Inter', 'system-ui', 'sans-serif'],
        body: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        // A deliberate scale rather than Tailwind's default ladder.
        micro: ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.08em' }],
        tiny: ['0.75rem', { lineHeight: '1.1rem' }],
      },
      animation: {
        // The only motion in the design, spent on the tick strip.
        sweep: 'sweep 2.4s cubic-bezier(0.4, 0, 0.2, 1) infinite',
        surface: 'surface 0.4s cubic-bezier(0.2, 0, 0.2, 1)',
        'slide-in': 'slide-in 0.28s cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        sweep: {
          '0%':   { transform: 'translateX(-100%)', opacity: '0' },
          '40%':  { opacity: '1' },
          '100%': { transform: 'translateX(220%)', opacity: '0' },
        },
        surface: {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to:   { opacity: '1', transform: 'none' },
        },
        'slide-in': {
          from: { transform: 'translateX(100%)' },
          to:   { transform: 'none' },
        },
      },
    },
  },
  plugins: [],
}
