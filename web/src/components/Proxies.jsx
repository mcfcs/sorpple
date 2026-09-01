/**
 * The proxy list behind Indeed.
 *
 * Indeed is the only source that costs money: every request goes through a paid
 * rotating proxy to get past Cloudflare. This is that list — browsable, and
 * editable without opening a 500 KB text file by hand.
 *
 * Two modes, because the two jobs are different: a table for finding one entry
 * among thousands, and a plain textarea for pasting in a whole new list. The
 * server refuses to write anything that isn't host:port:user:pass, so a bad
 * paste cannot quietly drop the bot to direct requests.
 */

import { useCallback, useEffect, useState } from 'react'
import { getProxies, getProxiesRaw, saveProxies } from '../api'
import { Button, Eyebrow, Stat } from './primitives'

const PAGE = 100

/** Credentials are the sensitive half, so they stay masked until asked for. */
function maskProxy(value, reveal) {
  const parts = value.split(':')
  if (reveal || parts.length !== 4) return value
  return `${parts[0]}:${parts[1]}:${'•'.repeat(8)}:${'•'.repeat(6)}`
}

export default function Proxies() {
  const [data, setData] = useState(null)
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [reveal, setReveal] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      const result = await getProxies({ offset, limit: PAGE, q: query })
      setData(result)
      setError(null)
    } catch (exc) {
      setError(exc.message)
    }
  }, [offset, query])

  useEffect(() => {
    load()
  }, [load])

  // Searching from page 5 should show the first match, not page 5 of matches.
  useEffect(() => {
    setOffset(0)
  }, [query])

  const openEditor = async () => {
    setNotice(null)
    try {
      const { text } = await getProxiesRaw()
      setDraft(text)
      setEditing(true)
    } catch (exc) {
      setError(exc.message)
    }
  }

  const copyAll = async () => {
    try {
      const { text } = await getProxiesRaw()
      await navigator.clipboard.writeText(text)
      setNotice({ kind: 'ok', text: 'The whole list is on your clipboard.' })
    } catch (exc) {
      setNotice({ kind: 'error', text: `Could not copy: ${exc.message}` })
    }
  }

  const save = async () => {
    setSaving(true)
    setNotice(null)
    try {
      const result = await saveProxies(draft)
      setNotice({
        kind: 'ok',
        text: `Saved ${result.total.toLocaleString()} proxies. ${result.note}`,
      })
      setEditing(false)
      await load()
    } catch (exc) {
      setNotice({ kind: 'error', text: exc.message })
    } finally {
      setSaving(false)
    }
  }

  const total = data?.total ?? 0
  const invalid = data?.invalid ?? 0
  const shown = data?.proxies ?? []
  const draftCount = draft
    .split('\n')
    .filter((line) => line.trim() && !line.trim().startsWith('#')).length

  return (
    <div className="space-y-5">
      {/* What this list is and what shape it's in. */}
      <section className="panel animate-surface p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="grid flex-1 grid-cols-2 gap-6 sm:grid-cols-3">
            <Stat value={total.toLocaleString()} label="Proxies" />
            <Stat
              value={invalid.toLocaleString()}
              label="Malformed"
              tone={invalid ? 'text-halt' : 'text-paper'}
            />
            {/* A filename is a label, not a measurement — it doesn't belong at
                the size of the counts beside it. */}
            <div>
              <div className="telemetry truncate text-sm text-paper-dim">
                {data?.path ?? 'proxies.txt'}
              </div>
              <div className="eyebrow mt-1.5">File</div>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="quiet" onClick={copyAll}>
              Copy all
            </Button>
            <Button size="sm" onClick={openEditor} disabled={editing}>
              Edit list
            </Button>
          </div>
        </div>

        <p className="mt-4 max-w-2xl text-tiny leading-relaxed text-paper-dim">
          Indeed reaches Cloudflare-protected pages through these. Prosple and
          JobStreet don&apos;t use them, so an empty list only affects Indeed —
          it falls back to direct requests and is likely to be blocked.
        </p>
      </section>

      {notice ? (
        <div
          className={`panel p-3.5 text-tiny ${
            notice.kind === 'error'
              ? 'border-halt/30 bg-halt/5 text-halt'
              : 'border-live/30 bg-live/5 text-live'
          }`}
          role="status"
        >
          {notice.text}
        </div>
      ) : null}

      {editing ? (
        <section className="panel animate-surface p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-display text-sm font-semibold">Edit the proxy list</h2>
              <p className="mt-1 text-tiny text-paper-faint">
                One per line as{' '}
                <code className="rounded bg-ink-700 px-1.5 py-0.5 font-mono">
                  host:port:user:password
                </code>
                . Lines starting with # are kept as comments.
              </p>
            </div>
            <span className="telemetry text-tiny text-paper-dim">
              {draftCount.toLocaleString()} proxies
            </span>
          </div>

          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            spellCheck={false}
            className="h-80 w-full resize-y rounded-md border border-ink-600 bg-ink-900/60 p-3
              font-mono text-tiny leading-relaxed text-paper"
          />

          <div className="mt-3 flex flex-wrap items-center gap-2.5">
            <Button onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save list'}
            </Button>
            <Button variant="quiet" onClick={() => setEditing(false)} disabled={saving}>
              Cancel
            </Button>
            <p className="text-tiny text-paper-faint">
              Nothing is written unless every line is valid.
            </p>
          </div>
        </section>
      ) : null}

      {/* The list itself. */}
      <section className="panel animate-surface">
        <div className="flex flex-wrap items-center gap-3 border-b border-ink-600/70 p-4">
          <div className="min-w-0 flex-1">
            <label htmlFor="proxy-search" className="sr-only">
              Search proxies
            </label>
            <input
              id="proxy-search"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by host, port or user…"
              className="w-full rounded-md border border-ink-600 bg-ink-900/60 px-3.5 py-2
                text-sm text-paper placeholder:text-paper-faint focus:border-ink-500"
            />
          </div>
          <Button size="sm" variant="quiet" onClick={() => setReveal((r) => !r)}>
            {reveal ? 'Hide credentials' : 'Show credentials'}
          </Button>
        </div>

        {error ? (
          <p className="p-6 text-tiny text-halt">Can&apos;t read the proxy list: {error}</p>
        ) : shown.length === 0 ? (
          <div className="p-10 text-center">
            <p className="font-display text-sm font-medium text-paper">
              {total === 0 && !query ? 'No proxies configured' : 'Nothing matches that search'}
            </p>
            <p className="mx-auto mt-2 max-w-sm text-tiny leading-relaxed text-paper-dim">
              {total === 0 && !query
                ? 'Indeed will fall back to direct requests, which Cloudflare usually blocks. Add a list to fix that.'
                : 'Try a different host or port.'}
            </p>
          </div>
        ) : (
          <>
            <ul className="divide-y divide-ink-600/40">
              {shown.map((proxy) => (
                <li
                  key={proxy.line}
                  className="flex items-center gap-3 px-4 py-2 hover:bg-ink-700/40"
                >
                  <span className="telemetry w-14 shrink-0 text-right text-tiny text-ink-500">
                    {proxy.line}
                  </span>
                  <code
                    className={`min-w-0 flex-1 truncate font-mono text-tiny ${
                      proxy.valid ? 'text-paper-dim' : 'text-halt'
                    }`}
                    title={reveal ? proxy.value : undefined}
                  >
                    {maskProxy(proxy.value, reveal)}
                  </code>
                  {!proxy.valid ? (
                    <span className="shrink-0 text-tiny text-halt">malformed</span>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => navigator.clipboard?.writeText(proxy.value)}
                    className="shrink-0 text-tiny text-paper-faint transition-colors hover:text-paper"
                  >
                    Copy
                  </button>
                </li>
              ))}
            </ul>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-600/70 p-4">
              <p className="telemetry text-tiny text-paper-faint">
                {offset + 1}–{Math.min(offset + PAGE, total)} of {total.toLocaleString()}
              </p>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="quiet"
                  onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
                  disabled={offset === 0}
                >
                  Previous
                </Button>
                <Button
                  size="sm"
                  variant="quiet"
                  onClick={() => setOffset((o) => o + PAGE)}
                  disabled={offset + PAGE >= total}
                >
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
