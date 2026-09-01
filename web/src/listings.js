/**
 * Deriving what a listing's state actually is.
 *
 * The three boards disagree about what they tell us, and the UI should be honest
 * about that rather than inventing certainty:
 *
 *   Prosple  keeps expired listings in the feed and flags them, and sometimes
 *            publishes a real deadline -> "closed" and "closing soon" are facts.
 *   SEEK and Indeed silently drop expired ads and publish no deadline at all ->
 *            we only know how long ago it was posted.
 *
 * So a listing is never guessed into "closed" from age alone. An old listing
 * with no deadline is "ageing", which says what we actually know.
 */

const DAY_MS = 24 * 60 * 60 * 1000

// Inside this window a deadline is worth acting on today.
export const CLOSING_SOON_DAYS = 7
// Past this, an undated listing is likely stale even though no board said so.
export const AGEING_DAYS = 30

export const STATUS = {
  closed: {
    key: 'closed',
    label: 'Closed',
    tone: 'text-paper-faint',
    dot: 'bg-paper-faint',
    // Closed listings stay in the list, dimmed — hiding them would make the
    // archive look smaller than it is and lose the "already ended" signal.
    dimmed: true,
  },
  soon: { key: 'soon', label: 'Closing soon', tone: 'text-soon', dot: 'bg-soon' },
  open: { key: 'open', label: 'Open', tone: 'text-live', dot: 'bg-live' },
  ageing: { key: 'ageing', label: 'Ageing', tone: 'text-paper-faint', dot: 'bg-ink-500' },
  unknown: { key: 'unknown', label: 'No deadline', tone: 'text-paper-dim', dot: 'bg-ink-500' },
}

export const STATUS_ORDER = ['open', 'soon', 'unknown', 'ageing', 'closed']

function parse(value) {
  if (!value) return null
  const time = Date.parse(value)
  return Number.isNaN(time) ? null : time
}

/**
 * @returns {{status: object, daysLeft: number|null, daysOld: number|null}}
 */
export function classify(listing, now = Date.now()) {
  const closesAt = parse(listing.closes_at)
  const postedAt = parse(listing.posted_at)

  const daysLeft = closesAt === null ? null : Math.floor((closesAt - now) / DAY_MS)
  const daysOld = postedAt === null ? null : Math.floor((now - postedAt) / DAY_MS)

  // The board's own verdict always wins.
  if (listing.closed) return { status: STATUS.closed, daysLeft, daysOld }

  if (closesAt !== null) {
    if (closesAt < now) return { status: STATUS.closed, daysLeft, daysOld }
    if (daysLeft <= CLOSING_SOON_DAYS) return { status: STATUS.soon, daysLeft, daysOld }
    return { status: STATUS.open, daysLeft, daysOld }
  }

  if (daysOld !== null && daysOld >= AGEING_DAYS) {
    return { status: STATUS.ageing, daysLeft, daysOld }
  }
  return { status: STATUS.unknown, daysLeft, daysOld }
}

/** "in 3 days" / "2 days ago" / "today" — the plain-language half of a date. */
export function relativeDays(days, { future }) {
  if (days === null) return null
  if (days === 0) return 'today'
  if (days === 1) return future ? 'tomorrow' : 'yesterday'
  return future ? `in ${days} days` : `${days} days ago`
}

export function formatDate(value) {
  const time = parse(value)
  if (time === null) return null
  return new Date(time).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

/** Sort keys the list offers. Newest-first is the default. */
export const SORTS = {
  newest: {
    label: 'Newest first',
    compare: (a, b) => (parse(b.posted_at) ?? 0) - (parse(a.posted_at) ?? 0),
  },
  closing: {
    label: 'Closing soonest',
    // Listings with no deadline sort last rather than pretending to be urgent.
    compare: (a, b) =>
      (parse(a.closes_at) ?? Infinity) - (parse(b.closes_at) ?? Infinity),
  },
  company: {
    label: 'Company A–Z',
    compare: (a, b) => (a.company || '').localeCompare(b.company || ''),
  },
  seen: {
    label: 'Recently found',
    compare: (a, b) => (parse(b.first_seen) ?? 0) - (parse(a.first_seen) ?? 0),
  },
}

/** Case- and accent-insensitive substring match across the fields worth searching. */
function haystack(listing) {
  return [
    listing.title,
    listing.company,
    listing.location,
    listing.classification,
    listing.work_type,
    listing.teaser,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
}

export function filterListings(listings, { query, sources, statuses, workTypes, years, sort }, now) {
  const needles = query.trim().toLowerCase().split(/\s+/).filter(Boolean)

  const result = listings.filter((listing) => {
    if (sources.size && !sources.has(listing.source)) return false

    if (workTypes.size && !workTypes.has(listing.work_type)) return false

    if (years.size && !(listing.years || []).some((y) => years.has(y))) return false

    if (statuses.size) {
      const { status } = classify(listing, now)
      if (!statuses.has(status.key)) return false
    }

    if (needles.length) {
      const text = haystack(listing)
      // Every term must appear — "manila intern" narrows rather than widens.
      if (!needles.every((needle) => text.includes(needle))) return false
    }

    return true
  })

  return result.sort(SORTS[sort]?.compare ?? SORTS.newest.compare)
}
