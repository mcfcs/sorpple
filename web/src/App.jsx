/**
 * Sorpple dashboard.
 *
 * Four surfaces: Control (what the monitor is doing, and changing it),
 * Internships (what it found), Console (the bot's log as it runs), and Proxies
 * (the paid list Indeed reaches Cloudflare through).
 *
 * Status is polled continuously because it drives every countdown; the archive
 * is fetched once and refreshed after a poll, since it only changes when the
 * bot posts something.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  POLL_MS,
  SETTLE_MS,
  getListings,
  getStatus,
  pauseSource,
  pollNow,
  resumeSource,
  setInterval_,
  setMonitor,
} from './api'
import CadenceStrip from './components/CadenceStrip'
import Console from './components/Console'
import Listings from './components/Listings'
import Proxies from './components/Proxies'
import SourceCard from './components/SourceCard'
import { Button, Eyebrow, Stat, Switch, useNow } from './components/primitives'

const TABS = [
  { key: 'control', label: 'Control' },
  { key: 'listings', label: 'Internships' },
  { key: 'console', label: 'Console' },
  { key: 'proxies', label: 'Proxies' },
]

export default function App() {
  const [tab, setTab] = useState('control')
  const [status, setStatus] = useState(null)
  const [listings, setListings] = useState([])
  const [statusError, setStatusError] = useState(null)
  const [listingsError, setListingsError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)
  const now = useNow()

  // Guards a fetch that resolves after the component is gone.
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const refreshStatus = useCallback(async () => {
    try {
      const data = await getStatus()
      if (!alive.current) return
      setStatus(data)
      setStatusError(null)
    } catch (error) {
      if (alive.current) setStatusError(error.message)
    }
  }, [])

  const refreshListings = useCallback(async () => {
    try {
      const data = await getListings()
      if (!alive.current) return
      setListings(data.listings || [])
      setListingsError(null)
    } catch (error) {
      if (alive.current) setListingsError(error.message)
    }
  }, [])

  useEffect(() => {
    refreshStatus()
    refreshListings()
    const id = window.setInterval(refreshStatus, POLL_MS)
    return () => window.clearInterval(id)
  }, [refreshStatus, refreshListings])

  /**
   * Run a control action, then re-read once the bot has had time to apply it.
   * Actions are queued, not applied inline, so the UI can only report what the
   * next status read shows.
   */
  const act = useCallback(
    async (fn, { message, reloadListings = false } = {}) => {
      setBusy(true)
      setNotice(null)
      try {
        const response = await fn()
        if (!alive.current) return
        if (response?.note) setNotice({ kind: 'warn', text: response.note })
        else if (message) setNotice({ kind: 'ok', text: message })

        window.setTimeout(() => {
          if (!alive.current) return
          refreshStatus()
          if (reloadListings) refreshListings()
        }, SETTLE_MS)
      } catch (error) {
        if (alive.current) setNotice({ kind: 'error', text: error.message })
      } finally {
        if (alive.current) setBusy(false)
      }
    },
    [refreshStatus, refreshListings],
  )

  const sources = status?.sources ?? []
  const anyRunning = sources.some((source) => source.running)
  const online = Boolean(status?.online)

  const handleToggle = (key, on) =>
    act(() => (on ? resumeSource(key) : pauseSource(key)), {
      message: `${on ? 'Resumed' : 'Paused'} ${key}.`,
    })

  const handleInterval = (key, minutes) =>
    act(() => setInterval_(key, minutes), {
      message: `${key} now polls every ${minutes} minutes.`,
    })

  const handlePoll = (key) =>
    act(() => pollNow(key), {
      message: `Polling ${key} now. New listings appear in Discord and here.`,
      reloadListings: true,
    })

  const handleMonitor = (on) =>
    act(() => setMonitor(on), {
      message: on ? 'Monitor on — all sources polling.' : 'Monitor off — all sources paused.',
    })

  return (
    <div className="min-h-screen">
      {/* Header: identity, live state, and the global switch. */}
      <header className="border-b border-ink-600/60 bg-ink-900/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-4 px-4 py-4 sm:px-6">
          <div className="flex items-center gap-3">
            <div className="flex gap-1" aria-hidden="true">
              <span className="h-2.5 w-2.5 rounded-full bg-prosple" />
              <span className="h-2.5 w-2.5 rounded-full bg-jobstreet" />
              <span className="h-2.5 w-2.5 rounded-full bg-indeed" />
            </div>
            <div>
              <h1 className="font-display text-lg font-bold leading-none tracking-tight">
                Sorpple
              </h1>
              <p className="mt-1 text-tiny text-paper-faint">
                Philippine internship monitor
              </p>
            </div>
          </div>

          <nav className="flex gap-1 rounded-lg bg-ink-800 p-1" aria-label="Views">
            {TABS.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                onClick={() => setTab(key)}
                aria-current={tab === key ? 'page' : undefined}
                className={`rounded-md px-3.5 py-1.5 text-tiny font-medium transition-colors
                  ${
                    tab === key
                      ? 'bg-ink-600 text-paper'
                      : 'text-paper-dim hover:text-paper'
                  }`}
              >
                {label}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-4">
            <div className="text-right">
              <div className="eyebrow">{online ? 'Bot online' : 'Bot offline'}</div>
              <div
                className={`mt-1 flex items-center justify-end gap-1.5 text-tiny
                  ${online ? 'text-live' : 'text-halt'}`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${online ? 'bg-live' : 'bg-halt'}`}
                />
                {online ? status?.bot_user || 'connected' : 'not running'}
              </div>
            </div>

            <div className="flex items-center gap-2.5 border-l border-ink-600 pl-4">
              <Eyebrow>Monitor</Eyebrow>
              {/* Offline, this cannot do anything: the bot is what applies it.
                  Disabling says so instead of flipping and silently queueing. */}
              <Switch
                checked={anyRunning}
                disabled={busy || !status || !online}
                title={online ? undefined : 'Start Sorpple to use the monitor switch'}
                onChange={handleMonitor}
              />
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6">
        {/* Offline is the one condition worth interrupting for: every control
            still works, but nothing takes effect until the bot is back. */}
        {tab === 'control' && status && !online ? (
          <div className="panel mb-5 border-halt/40 bg-halt/5 p-4">
            <p className="font-display text-sm font-semibold text-halt">
              Sorpple isn&apos;t running, so the controls are switched off.
            </p>
            <p className="mt-1.5 text-tiny leading-relaxed text-paper-dim">
              The bot is what polls the job boards — this page only tells it what
              to do. Start it and the controls come back on their own:
            </p>
            <p className="mt-2.5">
              <code className="rounded bg-ink-700 px-2 py-1 font-mono text-tiny text-paper">
                python sorpple.py
              </code>
              <span className="ml-2.5 text-tiny text-paper-faint">
                or run start-sorpple-web.bat, which starts both.
              </span>
            </p>
            <p className="mt-2.5 text-tiny text-paper-faint">
              The intervals below are your saved settings, not live values.
            </p>
          </div>
        ) : null}

        {tab === 'control' && statusError ? (
          <div className="panel mb-5 border-halt/30 bg-halt/5 p-4 text-sm text-halt">
            Can&apos;t reach the dashboard API: {statusError}
          </div>
        ) : null}

        {tab === 'control' && notice ? (
          <div
            className={`panel mb-5 p-3.5 text-tiny ${
              notice.kind === 'error'
                ? 'border-halt/30 bg-halt/5 text-halt'
                : notice.kind === 'warn'
                  ? 'border-soon/30 bg-soon/5 text-soon'
                  : 'border-live/30 bg-live/5 text-live'
            }`}
            role="status"
          >
            {notice.text}
          </div>
        ) : null}

        {tab === 'control' ? (
          !status ? (
            <p className="py-16 text-center text-sm text-paper-dim">Reading status…</p>
          ) : (
            <div className="space-y-5">
              <section className="panel animate-surface p-5 sm:p-6">
                <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
                  <Stat
                    value={sources.filter((s) => s.running).length}
                    label="Sources running"
                    tone={anyRunning ? 'text-live' : 'text-halt'}
                  />
                  <Stat value={listings.length.toLocaleString()} label="Archived" />
                  <Stat
                    value={sources
                      .reduce((total, s) => total + s.seen_count, 0)
                      .toLocaleString()}
                    label="Seen all-time"
                  />
                  <Stat
                    value={status.year_roles?.length ? status.year_roles.join(', ') : '—'}
                    label="Year pings"
                  />
                </div>
              </section>

              <CadenceStrip sources={sources} now={now} />

              <div className="grid gap-4 lg:grid-cols-3">
                {sources.map((source) => (
                  <SourceCard
                    key={source.key}
                    source={source}
                    now={now}
                    busy={busy}
                    online={online}
                    onToggle={handleToggle}
                    onInterval={handleInterval}
                    onPoll={handlePoll}
                  />
                ))}
              </div>
            </div>
          )
        ) : tab === 'listings' ? (
          <Listings listings={listings} now={now} error={listingsError} />
        ) : tab === 'console' ? (
          <Console online={online} />
        ) : (
          <Proxies />
        )}
      </main>

      <footer className="mx-auto max-w-6xl px-4 pb-8 pt-2 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-ink-600/60 pt-4">
          <p className="text-tiny text-paper-faint">
            Prosple · JobStreet · Indeed — Philippines
          </p>
          <Button size="sm" variant="quiet" onClick={refreshListings}>
            Refresh listings
          </Button>
        </div>
      </footer>
    </div>
  )
}
