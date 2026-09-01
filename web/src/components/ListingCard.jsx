/**
 * One internship, as a row you can scan and click.
 *
 * The whole card opens the detail panel, so reading a description is one click
 * from anywhere on the row rather than a small target. The links inside stop
 * their own clicks from bubbling, so "View on Prosple" still goes to Prosple.
 *
 * A closed listing is dimmed rather than removed: knowing an opportunity has
 * already ended answers a real question, and hiding it would make the archive
 * look thinner than it is.
 */

import { classify, formatDate, formatDateTime, relativeDays } from '../listings'
import { HUE, Pill, since } from './primitives'

function Logo({ listing, hue }) {
  if (listing.logo_url) {
    return (
      <img
        src={listing.logo_url}
        alt=""
        loading="lazy"
        className="h-11 w-11 shrink-0 rounded-lg bg-ink-700 object-contain p-1"
        onError={(event) => {
          event.currentTarget.style.visibility = 'hidden'
        }}
      />
    )
  }
  // No logo: the company initial in the source's hue keeps rows aligned and
  // still says where the listing came from.
  return (
    <div
      className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-lg
        ${hue.soft} font-display text-base font-semibold ${hue.text}`}
      aria-hidden="true"
    >
      {(listing.company || '?').charAt(0).toUpperCase()}
    </div>
  )
}

export default function ListingCard({ listing, now, onOpen }) {
  const hue = HUE[listing.source]
  const { status, daysLeft, daysOld } = classify(listing, now)

  // The metric line: only what is actually known, so no empty separators.
  const facts = [listing.location, listing.work_type, listing.salary].filter(Boolean)

  return (
    <article
      onClick={() => onOpen(listing)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onOpen(listing)
        }
      }}
      role="button"
      tabIndex={0}
      aria-label={`${listing.title} at ${listing.company}`}
      className={`panel animate-surface group relative cursor-pointer overflow-hidden p-4 pl-5
        transition-colors hover:border-ink-500 hover:bg-ink-700/40
        ${status.dimmed ? 'opacity-60 hover:opacity-100' : ''}`}
    >
      {/* The source's hue as a spine: origin readable before you read anything. */}
      <span
        className={`absolute inset-y-0 left-0 w-[3px] ${hue.bg} opacity-70
          transition-opacity group-hover:opacity-100`}
        aria-hidden="true"
      />

      <div className="flex gap-4">
        <Logo listing={listing} hue={hue} />

        <div className="min-w-0 flex-1">
          {/* Title + status. The title is the loudest thing on the row. */}
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h3 className="truncate font-display text-sm font-semibold leading-snug text-paper">
                {listing.title}
              </h3>
              <p className="mt-1 truncate text-tiny text-paper-dim">{listing.company}</p>
            </div>
            <Pill dot={status.dot} className={`shrink-0 ${status.tone}`}>
              {status.label}
            </Pill>
          </div>

          {facts.length ? (
            <p className="mt-2.5 truncate text-tiny text-paper-faint">{facts.join('  ·  ')}</p>
          ) : null}

          {listing.teaser ? (
            <p className="mt-2 line-clamp-2 text-tiny leading-relaxed text-paper-dim">
              {listing.teaser}
            </p>
          ) : null}

          {/* Footer: where it came from, when, and what it needs. */}
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-ink-600/50 pt-2.5">
            <span className={`eyebrow ${hue.text}`}>{listing.source}</span>

            {/* Two different clocks, and both matter: the board's posting date,
                and when Sorpple first saw it — the second is "new to me". */}
            <span
              className="telemetry text-tiny text-paper-faint"
              title={formatDateTime(listing.first_seen) || undefined}
            >
              added {since(listing.first_seen, now)}
            </span>

            {listing.posted_at || listing.posted_label ? (
              <span className="telemetry text-tiny text-paper-faint">
                posted {relativeDays(daysOld, { future: false }) ?? listing.posted_label}
              </span>
            ) : null}

            {listing.closes_at ? (
              <span
                className={`telemetry text-tiny ${
                  status.key === 'soon' ? 'text-soon' : 'text-paper-faint'
                }`}
              >
                {daysLeft !== null && daysLeft < 0
                  ? `closed ${formatDate(listing.closes_at)}`
                  : `closes ${relativeDays(daysLeft, { future: true })}`}
              </span>
            ) : null}

            {(listing.years || []).map((year) => (
              <span
                key={year}
                className="rounded bg-live/10 px-1.5 py-0.5 font-mono text-micro text-live"
                title={`Mentions ${year} — pinged the ${year} role`}
              >
                {year}
              </span>
            ))}

            {/* The affordance for the whole row, stated once. */}
            <span className="ml-auto text-tiny text-paper-faint transition-colors group-hover:text-paper">
              Read description →
            </span>
          </div>
        </div>
      </div>
    </article>
  )
}
