/**
 * The bot's log, live — the terminal window without the terminal.
 *
 * Sorpple normally runs in a console you have to be sitting at. This is the same
 * stream, tailed from sorpple.log, so you can see what it is doing from any
 * device on the tailnet.
 *
 * Lines are colour-coded by what they are rather than parsed into structure:
 * the log is written for a human reading a terminal, and the useful signal is
 * "which source, and did something go wrong".
 */

import { useEffect, useRef, useState } from 'react'
import { LOG_POLL_MS, getLog } from '../api'
import { Button, Eyebrow, Switch } from './primitives'

// A line looks like: [2026-09-02 06:12:09] [Prosple] No new listings.
const LINE = /^\[([\d-]+ [\d:]+)\]\s*(?:\[([^\]]+)\]\s*)?(.*)$/

const SOURCE_TONE = {
  prosple: 'text-prosple',
  jobstreet: 'text-jobstreet',
  indeed: 'text-indeed',
  control: 'text-paper-faint',
  web: 'text-paper-faint',
  archive: 'text-paper-faint',
}

function tone(message) {
  const text = message.toLowerCase()
  if (text.startsWith('error') || text.includes('traceback')) return 'text-halt'
  if (text.includes('error') || text.includes('failed')) return 'text-halt'
  if (text.startsWith('warning') || text.includes('warning')) return 'text-soon'
  if (text.includes('new listing') || text.includes('posted ')) return 'text-live'
  return 'text-paper-dim'
}

function Line({ raw }) {
  const match = LINE.exec(raw)
  if (!match) {
    return <div className="text-paper-dim">{raw}</div>
  }
  const [, stamp, tag, message] = match
  const tagTone = tag ? SOURCE_TONE[tag.toLowerCase()] ?? 'text-paper-dim' : ''

  return (
    <div className="flex gap-2.5 leading-relaxed">
      {/* The date repeats on every line and is rarely what you're reading, so
          only the clock is shown; the full stamp is in the title. */}
      <span className="shrink-0 text-ink-500" title={stamp}>
        {stamp.slice(11)}
      </span>
      {tag ? <span className={`shrink-0 ${tagTone}`}>{tag}</span> : null}
      <span className={`min-w-0 break-words ${tone(message)}`}>{message}</span>
    </div>
  )
}

export default function Console({ online }) {
  const [lines, setLines] = useState([])
  const [error, setError] = useState(null)
  const [missing, setMissing] = useState(false)
  const [follow, setFollow] = useState(true)
  const offset = useRef(0)
  const box = useRef(null)

  useEffect(() => {
    let alive = true

    const tick = async () => {
      try {
        const data = await getLog(offset.current)
        if (!alive) return
        offset.current = data.offset
        setMissing(Boolean(data.missing))
        setError(null)
        if (data.lines.length) {
          // Cap what is retained: the console is for watching, not archaeology.
          setLines((current) => [...current, ...data.lines].slice(-2000))
        }
      } catch (exc) {
        if (alive) setError(exc.message)
      }
    }

    tick()
    const id = window.setInterval(tick, LOG_POLL_MS)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [])

  // Stay pinned to the newest line unless the reader has scrolled up to read.
  useEffect(() => {
    if (follow && box.current) {
      box.current.scrollTop = box.current.scrollHeight
    }
  }, [lines, follow])

  const copy = () => {
    navigator.clipboard?.writeText(lines.join('\n'))
  }

  return (
    <div className="panel animate-surface flex h-[calc(100vh-13rem)] min-h-[26rem] flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-ink-600/70 px-4 py-3">
        <div>
          <h2 className="font-display text-sm font-semibold tracking-tight">Console</h2>
          <p className="mt-0.5 text-tiny text-paper-faint">
            {online ? 'Live from the running bot' : 'Sorpple is stopped — this is the last output'}
          </p>
        </div>

        <div className="ml-auto flex items-center gap-4">
          <span className="telemetry text-tiny text-paper-faint">
            {lines.length.toLocaleString()} lines
          </span>
          <Switch checked={follow} onChange={setFollow} label="Follow" />
          <Button size="sm" variant="quiet" onClick={copy} disabled={!lines.length}>
            Copy
          </Button>
          <Button size="sm" variant="quiet" onClick={() => setLines([])}>
            Clear
          </Button>
        </div>
      </header>

      <div
        ref={box}
        onScroll={(event) => {
          const el = event.currentTarget
          // Turn following off the moment you scroll away, back on at the bottom.
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
          if (atBottom !== follow) setFollow(atBottom)
        }}
        className="min-h-0 flex-1 overflow-y-auto bg-ink-900/50 p-4 font-mono text-tiny"
      >
        {error ? (
          <p className="text-halt">Can&apos;t read the log: {error}</p>
        ) : missing || !lines.length ? (
          <div className="flex h-full items-center justify-center text-center">
            <div>
              <p className="font-display text-sm font-medium text-paper">
                {missing ? 'No log file yet' : 'Waiting for output'}
              </p>
              <p className="mx-auto mt-2 max-w-sm leading-relaxed text-paper-faint">
                {missing
                  ? 'Sorpple writes sorpple.log as it runs. Start it and its output appears here.'
                  : 'The bot has not logged anything since it started.'}
              </p>
            </div>
          </div>
        ) : (
          lines.map((line, index) => <Line key={index} raw={line} />)
        )}
      </div>

      <footer className="shrink-0 border-t border-ink-600/70 px-4 py-2.5">
        <Eyebrow>
          Read-only — use the Control tab to change what the bot is doing
        </Eyebrow>
      </footer>
    </div>
  )
}
