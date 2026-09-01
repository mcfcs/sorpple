/**
 * The internships browser: search, filter, and the smart status view.
 *
 * Filters are multi-select toggles rather than dropdowns — with this few options
 * per facet, showing all of them costs nothing and saves a click each time.
 */

import { useEffect, useMemo, useState } from 'react'
import { SOURCE_META } from '../api'
import { SORTS, STATUS, STATUS_ORDER, classify, filterListings } from '../listings'
import ListingCard from './ListingCard'
import ListingDetail from './ListingDetail'
import { Button, Eyebrow, HUE } from './primitives'

/** Render more only as the reader asks for it — the archive can reach thousands. */
const PAGE_SIZE = 40

function useToggleSet(initial = []) {
  const [set, setSet] = useState(() => new Set(initial))
  const toggle = (value) =>
    setSet((current) => {
      const next = new Set(current)
      if (next.has(value)) next.delete(value)
      else next.add(value)
      return next
    })
  return [set, toggle, () => setSet(new Set())]
}

function FilterChip({
  active,
  onClick,
  children,
  activeClass = 'bg-paper text-ink-900',
  className = '',
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full border px-2.5 py-1 text-tiny font-medium transition-colors
        ${
          active
            ? `${activeClass} border-transparent`
            : 'border-ink-600 text-paper-dim hover:border-ink-500 hover:text-paper'
        } ${className}`}
    >
      {children}
    </button>
  )
}

function FilterGroup({ label, children }) {
  return (
    <div>
      <Eyebrow className="mb-2">{label}</Eyebrow>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  )
}

export default function Listings({ listings, now, error }) {
  const [selected, setSelected] = useState(null)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('newest')
  const [visible, setVisible] = useState(PAGE_SIZE)
  const [sources, toggleSource, clearSources] = useToggleSet()
  const [statuses, toggleStatus, clearStatuses] = useToggleSet()
  const [workTypes, toggleWorkType, clearWorkTypes] = useToggleSet()
  const [years, toggleYear, clearYears] = useToggleSet()

  // Facet values come from the data, so a new work type or intake year appears
  // as a filter without a code change.
  const facets = useMemo(() => {
    const work = new Set()
    const yearSet = new Set()
    for (const listing of listings) {
      if (listing.work_type) work.add(listing.work_type)
      for (const year of listing.years || []) yearSet.add(year)
    }
    return {
      workTypes: [...work].sort(),
      years: [...yearSet].sort(),
    }
  }, [listings])

  const counts = useMemo(() => {
    const tally = { total: listings.length }
    for (const key of STATUS_ORDER) tally[key] = 0
    for (const listing of listings) tally[classify(listing, now).status.key] += 1
    return tally
    // Recount on the minute, not every second — status only changes that slowly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listings, Math.floor(now / 60_000)])

  const filtered = useMemo(
    () => filterListings(listings, { query, sources, statuses, workTypes, years, sort }, now),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [listings, query, sources, statuses, workTypes, years, sort, Math.floor(now / 60_000)],
  )

  // Any change to the result set starts the reader at the top of a fresh page.
  useEffect(() => {
    setVisible(PAGE_SIZE)
  }, [query, sources, statuses, workTypes, years, sort])

  const filtering =
    query.trim() || sources.size || statuses.size || workTypes.size || years.size

  const clearAll = () => {
    setQuery('')
    clearSources()
    clearStatuses()
    clearWorkTypes()
    clearYears()
  }

  return (
    <div className="space-y-5">
      {/* Search + sort */}
      <div className="panel p-4 sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <label htmlFor="listing-search" className="sr-only">
              Search internships
            </label>
            <input
              id="listing-search"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search title, company, location…"
              className="w-full rounded-md border border-ink-600 bg-ink-900/60 px-3.5 py-2.5
                text-sm text-paper placeholder:text-paper-faint focus:border-ink-500"
            />
          </div>

          <div className="flex items-center gap-2">
            <label htmlFor="listing-sort" className="eyebrow shrink-0">
              Sort
            </label>
            <select
              id="listing-sort"
              value={sort}
              onChange={(event) => setSort(event.target.value)}
              // Native arrow removed for one drawn in the panel's own palette;
              // the default control is the wrong colour on a dark ground.
              className="cursor-pointer appearance-none rounded-md border border-ink-600
                bg-ink-900/60 bg-[length:0.65rem] bg-[right_0.7rem_center] bg-no-repeat
                py-2 pl-2.5 pr-7 text-sm text-paper hover:border-ink-500"
              style={{
                backgroundImage:
                  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 6' fill='none' stroke='%2394a6bb' stroke-width='1.5'%3E%3Cpath d='M1 1l4 4 4-4'/%3E%3C/svg%3E\")",
              }}
            >
              {Object.entries(SORTS).map(([key, { label }]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <FilterGroup label="Source">
            {Object.entries(SOURCE_META).map(([key, meta]) => (
              <FilterChip
                key={key}
                active={sources.has(key)}
                onClick={() => toggleSource(key)}
                activeClass={`${HUE[key].bg} text-ink-900`}
              >
                {meta.label}
              </FilterChip>
            ))}
          </FilterGroup>

          <FilterGroup label="Status">
            {/* A status nothing currently has is still worth showing — its
                absence is information — but it shouldn't compete for attention. */}
            {STATUS_ORDER.map((key) => (
              <FilterChip
                key={key}
                active={statuses.has(key)}
                onClick={() => toggleStatus(key)}
                className={counts[key] === 0 && !statuses.has(key) ? 'opacity-45' : ''}
              >
                {STATUS[key].label}
                <span className="telemetry ml-1.5 opacity-60">{counts[key]}</span>
              </FilterChip>
            ))}
          </FilterGroup>

          {facets.workTypes.length ? (
            <FilterGroup label="Work type">
              {facets.workTypes.map((type) => (
                <FilterChip
                  key={type}
                  active={workTypes.has(type)}
                  onClick={() => toggleWorkType(type)}
                >
                  {type}
                </FilterChip>
              ))}
            </FilterGroup>
          ) : null}

          {facets.years.length ? (
            <FilterGroup label="Intake year">
              {facets.years.map((year) => (
                <FilterChip
                  key={year}
                  active={years.has(year)}
                  onClick={() => toggleYear(year)}
                  activeClass="bg-live text-ink-900"
                >
                  {year}
                </FilterChip>
              ))}
            </FilterGroup>
          ) : null}
        </div>
      </div>

      {/* Result count + clear */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-tiny text-paper-dim">
          <span className="telemetry text-paper">{filtered.length.toLocaleString()}</span>
          {filtering ? ` of ${counts.total.toLocaleString()} listings` : ' listings'}
        </p>
        {filtering ? (
          <Button size="sm" variant="quiet" onClick={clearAll}>
            Clear filters
          </Button>
        ) : null}
      </div>

      {error ? (
        <div className="panel p-6 text-sm text-halt">{error}</div>
      ) : filtered.length === 0 ? (
        <div className="panel p-10 text-center">
          <p className="font-display text-sm font-medium text-paper">
            {counts.total === 0 ? 'No listings archived yet' : 'Nothing matches those filters'}
          </p>
          <p className="mx-auto mt-2 max-w-sm text-tiny leading-relaxed text-paper-dim">
            {counts.total === 0
              ? 'Run python sorpple.py --backfill to fill the archive from the current boards, or wait for the next poll to bring one in.'
              : 'Widen the search or clear a filter to see more.'}
          </p>
          {filtering && counts.total > 0 ? (
            <Button size="sm" className="mt-4" onClick={clearAll}>
              Clear filters
            </Button>
          ) : null}
        </div>
      ) : (
        <>
          <div className="space-y-2.5">
            {filtered.slice(0, visible).map((listing) => (
              <ListingCard
                key={`${listing.source}:${listing.id}`}
                listing={listing}
                now={now}
                onOpen={setSelected}
              />
            ))}
          </div>

          {visible < filtered.length ? (
            <div className="pt-1 text-center">
              <Button onClick={() => setVisible((count) => count + PAGE_SIZE)}>
                Show {Math.min(PAGE_SIZE, filtered.length - visible)} more
              </Button>
            </div>
          ) : null}
        </>
      )}

      {selected ? (
        <ListingDetail
          listing={selected}
          now={now}
          onClose={() => setSelected(null)}
        />
      ) : null}
    </div>
  )
}
