/**
 * One internship.
 *
 * A closed listing is dimmed rather than removed: knowing an opportunity has
 * already ended is the answer to a real question, and hiding it would just make
 * the archive look thinner than it is.
 */

import { classify, formatDate, relativeDays } from '../listings'
import { HUE, Pill } from './primitives'

function Logo({ listing, hue }) {
  if (listing.logo_url) {
    return (
      <img
        src={listing.logo_url}
        alt=""
        loading="lazy"
        className="h-10 w-10 shrink-0 rounded-md bg-ink-700 object-contain p-1"
        onError={(event) => {
          event.currentTarget.style.display = 'none'
        }}
      />
    )
  }
  // No logo: the company initial in the source's hue keeps the row aligned and
  // still says where the listing came from.
  return (
    <div
      className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-md
        ${hue.soft} font-display text-sm font-semibold ${hue.text}`}
      aria-hidden="true"
    >
      {(listing.company || '?').charAt(0).toUpperCase()}
    </div>
  )
}

export default function ListingCard({ listing, now }) {
  const hue = HUE[listing.source]
  const { status, daysLeft, daysOld } = classify(listing, now)

  const facts = [listing.location, listing.work_type, listing.salary].filter(Boolean)

  return (
    <article
      className={`panel animate-surface group relative flex gap-4 p-4 transition-colors
        hover:border-ink-500/70 ${status.dimmed ? 'opacity-55 hover:opacity-90' : ''}`}
    >
      {/* The source's hue as a spine down the left edge — origin readable before
          you read anything. */}
      <span
        className={`absolute inset-y-3 left-0 w-0.5 rounded-full ${hue.bg} opacity-60`}
        aria-hidden="true"
      />

      <Logo listing={listing} hue={hue} />

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate font-display text-sm font-semibold leading-snug">
              <a
                href={listing.url}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:underline focus-visible:underline"
              >
                {listing.title}
              </a>
            </h3>
            <p className="mt-0.5 truncate text-tiny text-paper-dim">{listing.company}</p>
          </div>

          <Pill dot={status.dot} className={`shrink-0 ${status.tone}`}>
            {status.label}
          </Pill>
        </div>

        {facts.length ? (
          <p className="mt-2 truncate text-tiny text-paper-faint">{facts.join(' · ')}</p>
        ) : null}

        {listing.teaser ? (
          <p className="mt-2 line-clamp-2 text-tiny leading-relaxed text-paper-dim">
            {listing.teaser}
          </p>
        ) : null}

        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className={`eyebrow ${hue.text}`}>{listing.source}</span>

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
                : `closes ${formatDate(listing.closes_at)} · ${relativeDays(daysLeft, {
                    future: true,
                  })}`}
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

          {listing.apply_url ? (
            <a
              href={listing.apply_url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto text-tiny text-paper-dim underline-offset-2 hover:text-paper hover:underline"
            >
              Apply direct
            </a>
          ) : null}
        </div>
      </div>
    </article>
  )
}
