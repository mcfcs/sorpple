/** Shared building blocks: the pieces used by both views. */

import { useEffect, useState } from 'react'

/** Tailwind can't build class names at runtime, so map source -> classes here. */
export const HUE = {
  prosple: {
    text: 'text-prosple',
    bg: 'bg-prosple',
    border: 'border-prosple/40',
    soft: 'bg-prosple/10',
    ring: 'ring-prosple/30',
  },
  jobstreet: {
    text: 'text-jobstreet',
    bg: 'bg-jobstreet',
    border: 'border-jobstreet/40',
    soft: 'bg-jobstreet/10',
    ring: 'ring-jobstreet/30',
  },
  indeed: {
    text: 'text-indeed',
    bg: 'bg-indeed',
    border: 'border-indeed/40',
    soft: 'bg-indeed/10',
    ring: 'ring-indeed/30',
  },
}

export function Eyebrow({ children, className = '' }) {
  return <div className={`eyebrow ${className}`}>{children}</div>
}

/**
 * A count with its label. The number is the loud part; the label stays quiet,
 * so a row of these reads as data rather than as decoration.
 */
export function Stat({ value, label, tone = 'text-paper' }) {
  return (
    <div>
      <div className={`telemetry text-2xl font-semibold leading-none ${tone}`}>{value}</div>
      <div className="eyebrow mt-1.5">{label}</div>
    </div>
  )
}

export function Button({
  children,
  onClick,
  disabled,
  variant = 'default',
  size = 'md',
  className = '',
  ...rest
}) {
  const variants = {
    default:
      'bg-ink-700 text-paper hover:bg-ink-600 border border-ink-600 disabled:hover:bg-ink-700',
    quiet:
      'bg-transparent text-paper-dim hover:text-paper hover:bg-ink-700 border border-transparent',
    danger: 'bg-halt/15 text-halt hover:bg-halt/25 border border-halt/30',
  }
  const sizes = {
    sm: 'px-2.5 py-1 text-tiny',
    md: 'px-3.5 py-2 text-sm',
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md font-medium transition-colors disabled:cursor-not-allowed
        disabled:opacity-40 ${variants[variant]} ${sizes[size]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}

/**
 * On/off control. A real checkbox underneath, so it is reachable by keyboard and
 * announced correctly; the visual switch is drawn on top.
 */
export function Switch({ checked, onChange, disabled, label, title, hue = 'live' }) {
  // Tailwind only keeps classes it can see in the source, so this maps rather
  // than interpolating `bg-${hue}` — that class would never be generated.
  const onColor = { live: 'bg-live', ...Object.fromEntries(
    Object.entries(HUE).map(([key, value]) => [key, value.bg]),
  ) }[hue] ?? 'bg-live'

  return (
    <label
      title={title}
      className={`inline-flex items-center gap-2.5 ${
        disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'
      }`}
    >
      <span className="relative inline-block h-5 w-9 shrink-0">
        <input
          type="checkbox"
          className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span
          className={`pointer-events-none absolute inset-0 rounded-full transition-colors
            peer-focus-visible:ring-2 peer-focus-visible:ring-paper/70
            peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-ink-800
            ${checked ? onColor : 'bg-ink-600'}`}
        />
        <span
          className={`pointer-events-none absolute top-0.5 h-4 w-4 rounded-full bg-ink-900
            shadow transition-transform ${checked ? 'translate-x-[1.125rem]' : 'translate-x-0.5'}`}
        />
      </span>
      {label ? <span className="text-sm text-paper-dim">{label}</span> : null}
    </label>
  )
}

/** A small status dot with its word, used wherever state is reported. */
export function Pill({ dot, children, className = '' }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-tiny ${className}`}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      {children}
    </span>
  )
}

/** Ticks once a second — the single clock every countdown on the page reads. */
export function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}

/** "4m 12s" / "38s" — compact enough to sit inline without wrapping. */
export function countdown(targetIso, now) {
  if (!targetIso) return null
  const remaining = Date.parse(targetIso) - now
  if (Number.isNaN(remaining)) return null
  if (remaining <= 0) return 'now'

  const totalSeconds = Math.floor(remaining / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60

  if (hours) return `${hours}h ${String(minutes).padStart(2, '0')}m`
  if (minutes) return `${minutes}m ${String(seconds).padStart(2, '0')}s`
  return `${seconds}s`
}

/** "3 minutes ago" for a past timestamp, or an em dash when there isn't one. */
export function since(iso, now) {
  if (!iso) return '—'
  const elapsed = now - Date.parse(iso)
  if (Number.isNaN(elapsed)) return '—'
  if (elapsed < 60_000) return 'just now'

  const minutes = Math.floor(elapsed / 60_000)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}
