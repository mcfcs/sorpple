/**
 * The cadence strip — the one thing this dashboard should be remembered by.
 *
 * Three lanes, one per source, each a track showing where that source is in its
 * polling cycle: the bar fills as its interval elapses and resets on each poll.
 * Lane width is proportional to the interval, so Indeed's 30-minute cycle is
 * visibly a longer journey than Prosple's 10 — the cost difference that shapes
 * the whole product, made legible without a word of explanation.
 *
 * This is where the design spends its motion. Everything else holds still.
 */

import { HUE } from './primitives'
import { countdown } from './primitives'

function progress(source, now) {
  // Fraction of this source's interval already elapsed, from the next-run time
  // the bot publishes. Without a live next_run there is no cycle to draw.
  if (!source.running || !source.next_run) return null
  const next = Date.parse(source.next_run)
  if (Number.isNaN(next)) return null

  const total = source.minutes * 60_000
  const remaining = next - now
  if (remaining <= 0) return 1
  if (remaining > total) return 0
  return 1 - remaining / total
}

export default function CadenceStrip({ sources, now }) {
  const longest = Math.max(...sources.map((s) => s.minutes), 1)

  return (
    <div className="panel animate-surface p-5 sm:p-6">
      <div className="mb-5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="font-display text-sm font-semibold tracking-tight">Poll cadence</h2>
        <p className="text-tiny text-paper-faint">
          Lane length is the interval; the fill is the cycle in progress.
        </p>
      </div>

      <div className="space-y-3.5">
        {sources.map((source) => {
          const hue = HUE[source.key]
          const filled = progress(source, now)
          const width = Math.max((source.minutes / longest) * 100, 12)
          const remaining = countdown(source.next_run, now)

          return (
            <div key={source.key} className="grid grid-cols-[5.5rem_1fr] items-center gap-3">
              <div className={`truncate font-display text-tiny font-medium ${hue.text}`}>
                {source.label}
              </div>

              <div className="flex items-center gap-3">
                {/* The track is sized to the interval, so the lanes compare. */}
                <div
                  className="relative h-2 overflow-hidden rounded-full bg-ink-700"
                  style={{ width: `${width}%` }}
                >
                  {source.running ? (
                    // The sweep lives inside the fill and is clipped by it, so
                    // it reads as movement within the elapsed part of the cycle.
                    // Floating it over the empty lane instead just looked like a
                    // stray dot running ahead of the bar.
                    <div
                      className={`relative h-full overflow-hidden rounded-full ${hue.bg}
                        transition-[width] duration-1000 ease-linear`}
                      style={{ width: `${(filled ?? 0) * 100}%` }}
                    >
                      <div className="absolute inset-y-0 left-0 w-1/3 animate-sweep bg-white/40 blur-[3px]" />
                    </div>
                  ) : (
                    // Paused lanes are drawn, not hidden — the gap in coverage
                    // is the information.
                    <div className="h-full w-full bg-[repeating-linear-gradient(115deg,transparent,transparent_5px,rgb(51_70_90/0.7)_5px,rgb(51_70_90/0.7)_10px)]" />
                  )}
                </div>

                <div className="telemetry shrink-0 text-tiny text-paper-faint">
                  {source.running ? (
                    <>
                      <span className="text-paper-dim">{remaining ?? '—'}</span>
                      <span className="mx-1.5 text-ink-500">/</span>
                      {source.minutes}m
                    </>
                  ) : (
                    <span className="text-paper-faint">paused</span>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
