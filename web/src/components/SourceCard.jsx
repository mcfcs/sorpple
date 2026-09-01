/**
 * One job board's controls: on/off, how often it polls, and a manual poll.
 *
 * The interval field commits on blur or Enter rather than on every keystroke —
 * typing "30" should not queue an action for "3" on the way.
 */

import { useEffect, useState } from 'react'
import { INDEED_CHEAP_MINUTES, MAX_MINUTES, MIN_MINUTES, SOURCE_META } from '../api'
import { Button, HUE, Pill, Switch, countdown, since } from './primitives'

export default function SourceCard({
  source,
  now,
  busy,
  online,
  onToggle,
  onInterval,
  onPoll,
}) {
  // Every control here needs the bot to carry it out, so with Sorpple stopped
  // they are disabled rather than queueing an action nothing will apply.
  const locked = busy || !online
  const lockedReason = online ? undefined : 'Start Sorpple to use this'
  const hue = HUE[source.key]
  const meta = SOURCE_META[source.key]
  const [draft, setDraft] = useState(String(source.minutes))

  // Follow the server's value unless the field is being edited right now.
  useEffect(() => {
    setDraft((current) =>
      Number(current) === source.minutes ? current : String(source.minutes),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.minutes])

  const parsed = Number(draft)
  const valid = Number.isInteger(parsed) && parsed >= MIN_MINUTES && parsed <= MAX_MINUTES
  const changed = valid && parsed !== source.minutes

  const commit = () => {
    if (!changed) {
      setDraft(String(source.minutes))
      return
    }
    onInterval(source.key, parsed)
  }

  // The one piece of domain advice worth surfacing: Indeed costs money per poll.
  const pricey = source.key === 'indeed' && valid && parsed < INDEED_CHEAP_MINUTES

  return (
    <article
      className={`panel animate-surface flex flex-col gap-5 p-5 transition-opacity
        ${source.running ? '' : 'opacity-75'}`}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 shrink-0 rounded-full ${hue.bg}`} />
            <h3 className="truncate font-display text-base font-semibold tracking-tight">
              {source.label}
            </h3>
          </div>
          <p className="mt-1 text-tiny text-paper-faint">{meta.cost}</p>
        </div>

        <Switch
          checked={source.running}
          disabled={locked}
          title={lockedReason}
          hue={source.key}
          onChange={(on) => onToggle(source.key, on)}
        />
      </header>

      {/* State line: what it is doing right now. Always one line tall, so a
          paused card doesn't collapse and leave the row ragged. */}
      <div className="flex min-h-[1.25rem] flex-wrap items-center gap-x-4 gap-y-1.5">
        {source.running ? (
          <>
            <Pill dot="bg-live" className="text-live">
              Running
            </Pill>
            <span className="telemetry text-tiny text-paper-dim">
              next in {countdown(source.next_run, now) ?? '—'}
            </span>
          </>
        ) : (
          <>
            <Pill dot="bg-halt" className="text-halt">
              Paused
            </Pill>
            <span className="text-tiny text-paper-faint">not polling</span>
          </>
        )}
      </div>

      {/* Telemetry row. Mono + tabular so nothing shifts as these update. */}
      <dl className="grid grid-cols-3 gap-3 border-y border-ink-600/60 py-3.5">
        {[
          ['Last poll', since(source.last_poll, now)],
          ['New then', source.last_new],
          ['Seen', source.seen_count.toLocaleString()],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="eyebrow">{label}</dt>
            <dd className="telemetry mt-1 text-sm text-paper">{value}</dd>
          </div>
        ))}
      </dl>

      {/* Interval + manual poll. */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <label
            className="eyebrow mb-1.5 block"
            htmlFor={`interval-${source.key}`}
          >
            Poll every
          </label>
          <div className="flex items-center gap-2">
            <input
              id={`interval-${source.key}`}
              type="number"
              inputMode="numeric"
              min={MIN_MINUTES}
              max={MAX_MINUTES}
              value={draft}
              disabled={locked}
              title={lockedReason}
              onChange={(event) => setDraft(event.target.value)}
              onBlur={commit}
              onKeyDown={(event) => {
                if (event.key === 'Enter') event.currentTarget.blur()
                if (event.key === 'Escape') setDraft(String(source.minutes))
              }}
              className={`telemetry w-20 rounded-md border bg-ink-900/60 px-2.5 py-1.5 text-sm
                text-paper transition-colors disabled:opacity-50
                ${valid ? 'border-ink-600' : 'border-halt'}`}
            />
            <span className="text-tiny text-paper-faint">minutes</span>
            {changed ? (
              <Button size="sm" variant="quiet" onClick={commit} disabled={locked}>
                Apply
              </Button>
            ) : null}
          </div>
        </div>

        <Button
          size="sm"
          onClick={() => onPoll(source.key)}
          disabled={locked}
          title={lockedReason}
        >
          Poll now
        </Button>
      </div>

      {!valid ? (
        <p className="text-tiny text-halt">
          Enter a whole number of minutes between {MIN_MINUTES} and {MAX_MINUTES}.
        </p>
      ) : null}

      {pricey ? (
        <p className="rounded-md bg-soon/10 px-3 py-2 text-tiny text-soon">
          Indeed goes through paid proxies. Polling every {parsed} minutes will
          spend them quickly — {INDEED_CHEAP_MINUTES} minutes or more is cheaper.
        </p>
      ) : null}

      {source.last_error ? (
        <p className="break-words rounded-md bg-halt/10 px-3 py-2 font-mono text-tiny text-halt">
          {source.last_error}
        </p>
      ) : null}
    </article>
  )
}
