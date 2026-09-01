/**
 * The full listing, in a panel that slides in over the list.
 *
 * A panel rather than a modal or a separate page: you keep your place in the
 * results, and the description arrives in the same reading position each time,
 * which is what makes scanning a shortlist quick.
 *
 * The description is fetched the first time a listing is opened and cached by
 * the server afterwards. That laziness is the point — every Indeed fetch spends
 * a paid proxy request, so descriptions are pulled for listings you actually
 * look at rather than all 75 up front.
 */

import { useEffect, useRef, useState } from 'react'
import { SOURCE_META, getDescription } from '../api'
import { classify, formatDate, formatDateTime, relativeDays } from '../listings'
import { Markdown } from '../markdown'
import { Button, HUE, Pill, since } from './primitives'

function Fact({ label, children }) {
  if (!children) return null
  return (
    <div className="min-w-0">
      <dt className="eyebrow">{label}</dt>
      <dd className="mt-1 text-tiny leading-snug text-paper">{children}</dd>
    </div>
  )
}

export default function ListingDetail({ listing, now, onClose }) {
  const [description, setDescription] = useState(null)
  const [state, setState] = useState('loading')
  const panel = useRef(null)
  const closeButton = useRef(null)

  const hue = HUE[listing.source]
  const sourceLabel = SOURCE_META[listing.source]?.label ?? listing.source
  const { status, daysLeft, daysOld } = classify(listing, now)

  // Fetch on open; re-fetch when a different listing is selected.
  useEffect(() => {
    let cancelled = false
    setState('loading')
    setDescription(null)

    getDescription(listing.source, listing.id)
      .then((data) => {
        if (cancelled) return
        setDescription(data.description)
        setState(data.description ? 'ready' : 'empty')
      })
      .catch(() => {
        if (!cancelled) setState('error')
      })

    return () => {
      cancelled = true
    }
  }, [listing.source, listing.id])

  // Move focus into the panel so the keyboard follows what opened, and send it
  // back out on Escape.
  useEffect(() => {
    closeButton.current?.focus()
    const onKey = (event) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <button
        type="button"
        aria-label="Close listing"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-ink-900/70 backdrop-blur-sm"
      />

      <aside
        ref={panel}
        className="relative flex h-full w-full max-w-xl flex-col border-l border-ink-600
          bg-ink-800 shadow-2xl motion-safe:animate-slide-in"
      >
        {/* Header — stays put while the description scrolls. */}
        <header className="shrink-0 border-b border-ink-600/70 p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="mb-2 flex items-center gap-2.5">
                <span className={`eyebrow ${hue.text}`}>{listing.source}</span>
                <Pill dot={status.dot} className={status.tone}>
                  {status.label}
                </Pill>
              </div>
              <h2 className="font-display text-lg font-semibold leading-snug text-paper">
                {listing.title}
              </h2>
              <p className="mt-1 text-sm text-paper-dim">{listing.company}</p>
            </div>

            <Button
              ref={closeButton}
              variant="quiet"
              size="sm"
              onClick={onClose}
              aria-label="Close"
              className="shrink-0"
            >
              Close
            </Button>
          </div>

          <div className="mt-4 flex flex-wrap gap-2.5">
            <a
              href={listing.url}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md border border-ink-600 bg-ink-700 px-3.5 py-2 text-tiny
                font-medium text-paper transition-colors hover:bg-ink-600"
            >
              View on {sourceLabel}
            </a>
            {listing.apply_url ? (
              <a
                href={listing.apply_url}
                target="_blank"
                rel="noopener noreferrer"
                className={`rounded-md px-3.5 py-2 text-tiny font-semibold text-ink-900
                  transition-opacity hover:opacity-90 ${hue.bg}`}
              >
                Apply on company site
              </a>
            ) : null}
          </div>
        </header>

        {/* Facts. Dates say both when the board posted it and when Sorpple found
            it — they differ, and the second is what "new to me" actually means. */}
        <dl className="grid shrink-0 grid-cols-2 gap-4 border-b border-ink-600/70 p-5 sm:grid-cols-3">
          <Fact label="Location">{listing.location}</Fact>
          <Fact label="Work type">{listing.work_type}</Fact>
          <Fact label="Salary">{listing.salary || 'Not disclosed'}</Fact>
          <Fact label="Field">{listing.classification}</Fact>
          <Fact label="Starts">{listing.start_date}</Fact>
          <Fact label="Posted">
            {listing.posted_at
              ? `${formatDate(listing.posted_at)} · ${relativeDays(daysOld, { future: false })}`
              : listing.posted_label}
          </Fact>
          <Fact label="Closes">
            {listing.closes_at
              ? `${formatDate(listing.closes_at)}${
                  daysLeft !== null && daysLeft >= 0
                    ? ` · ${relativeDays(daysLeft, { future: true })}`
                    : ''
                }`
              : null}
          </Fact>
          <Fact label="Sorpple found it">
            <span title={formatDateTime(listing.first_seen) || undefined}>
              {since(listing.first_seen, now)}
            </span>
          </Fact>
          {listing.years?.length ? (
            <Fact label="Intake year">{listing.years.join(', ')}</Fact>
          ) : null}
        </dl>

        {/* Description. */}
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <h3 className="eyebrow mb-3">Job description</h3>

          {state === 'loading' ? (
            <div className="space-y-2.5" aria-live="polite">
              <p className="text-tiny text-paper-faint">Fetching it from {sourceLabel}…</p>
              {[92, 78, 96, 64].map((width, i) => (
                <div
                  key={i}
                  className="h-3 animate-pulse rounded bg-ink-700"
                  style={{ width: `${width}%`, animationDelay: `${i * 90}ms` }}
                />
              ))}
            </div>
          ) : state === 'error' ? (
            <p className="text-tiny text-halt">
              The description could not be fetched. Open it on {sourceLabel} instead.
            </p>
          ) : state === 'empty' ? (
            <p className="text-tiny text-paper-faint">
              {listing.teaser ||
                `${sourceLabel} did not publish a description for this listing.`}
            </p>
          ) : (
            <Markdown text={description} />
          )}
        </div>
      </aside>
    </div>
  )
}
