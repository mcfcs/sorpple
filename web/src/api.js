/**
 * Talking to sorpple_web.py.
 *
 * Every control action is queued for the bot rather than applied directly, so a
 * successful response means "accepted", not "done". Callers re-read status a
 * moment later to see the effect — see POLL_MS / SETTLE_MS below.
 */

export const SOURCES = ['prosple', 'jobstreet', 'indeed']

export const SOURCE_META = {
  prosple: {
    label: 'Prosple',
    hue: 'prosple',
    // Shown on the control card so the interval decision has its reason attached.
    cost: 'GraphQL API · free',
  },
  jobstreet: { label: 'JobStreet', hue: 'jobstreet', cost: 'SSR HTML · free' },
  indeed: { label: 'Indeed', hue: 'indeed', cost: 'proxied · paid per request' },
}

// Indeed spends a paid proxy request per poll; below this the cost climbs fast.
// Mirrors INDEED_CHEAP_MINUTES in sorpple_commands.py.
export const INDEED_CHEAP_MINUTES = 15

export const MIN_MINUTES = 1
export const MAX_MINUTES = 1440

// Status is cheap to fetch and drives every countdown on the page.
export const POLL_MS = 5000
// The bot drains the control queue every 2s, then republishes its heartbeat on
// the following tick, so a change can take ~4-5s to show up in /api/status --
// measured at 4.46s. 2600ms re-read while the server still had the old value,
// which is what made a flipped switch appear to snap back. The UI now holds the
// intent until the server agrees, so this only sets when the control unlocks.
export const SETTLE_MS = 5200
// The console should feel live without hammering the disk.
export const LOG_POLL_MS = 2000

async function request(path, options) {
  const response = await fetch(path, options)
  const text = await response.text()

  let body = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      throw new Error(`Unexpected response from ${path}`)
    }
  }

  if (!response.ok) {
    throw new Error(body?.error || `${path} failed (${response.status})`)
  }
  return body
}

export const getStatus = () => request('/api/status')
export const getListings = () => request('/api/listings')

// Fetched from the job board on first ask, then cached server-side — so opening
// the same listing twice never spends a second proxy request.
export const getDescription = (source, id) =>
  request(`/api/description/${source}/${encodeURIComponent(id)}`)

// The console tails the log by byte offset, so each poll ships only new lines.
export const getLog = (since = 0) => request(`/api/log?since=${since}`)

export const getProxies = ({ offset = 0, limit = 100, q = '' } = {}) =>
  request(`/api/proxies?offset=${offset}&limit=${limit}&q=${encodeURIComponent(q)}`)

export const getProxiesRaw = () => request('/api/proxies?raw=1')

export const saveProxies = (text) =>
  request('/api/proxies', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })

function post(action, payload) {
  return request(`/api/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export const setInterval_ = (source, minutes) =>
  post('interval', { source, value: minutes })
export const pauseSource = (source) => post('pause', { source })
export const resumeSource = (source) => post('resume', { source })
export const setMonitor = (on, source = 'all') =>
  post('monitor', { source, value: on })
export const pollNow = (source) => post('poll', { source })
