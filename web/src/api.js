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
// The bot drains the control queue every 2s; wait past that before re-reading.
export const SETTLE_MS = 2600

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
