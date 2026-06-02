# Sorpple

**Sorpple** is a Discord bot that monitors multiple Philippine job boards for
new internship listings and posts rich embeds to a Discord channel. On the first
run it seeds the channel with the latest listings; from then on it pings whenever
a brand-new listing appears.

Three sources are monitored in parallel:

| Source | URL monitored | Bot script | Monitor script |
|--------|--------------|------------|----------------|
| **Prosple PH** | ph.prosple.com | `prosple_bot.py` | `prosple_monitor.py` |
| **Indeed PH** | ph.indeed.com — intern jobs, Philippines, sorted by date | `indeed_bot.py` | `indeed_monitor.py` |
| **JobStreet PH** | ph.jobstreet.com — "intern" jobs, Philippines, sorted by date | `jobstreet_bot.py` | `jobstreet_monitor.py` |

Each source has two scripts: a **bot** (interactive, with buttons) and a
**monitor** (zero extra dependencies, webhook or basic bot, supports `--once`).

---

## How each source works

### Prosple
Calls Prosple's GraphQL search API (persisted query
`OpportunitiesSearchWithoutStudyFieldFacetsModernLocations`), filtered to
internships in the Philippines, sorted newest-first. Full job descriptions come
from a supplementary `GetOpportunitySupplementaryDetails` query. No proxies needed.

### Indeed
Fetches `ph.indeed.com/jobs?q=intern&l=Philippines&sort=date` as HTML and
extracts job data from the `window.mosaic.providerData["mosaic-provider-jobcards"]`
JSON blob embedded in the page. Job descriptions are fetched by reloading the
same search URL with `?vjk={jobKey}` so Indeed renders the full detail panel
server-side — avoiding the separately Cloudflare-protected `/viewjob` endpoint.
**Proxies are strongly recommended** (proxies.txt, Wolveproxy format).

### JobStreet
Fetches `ph.jobstreet.com/%22intern%22-jobs/in-Philippines?sortmode=ListedDate`
as HTML and extracts all 30 results from the `window.SEEK_REDUX_DATA` JSON blob
(SEEK's SSR Redux state). Job descriptions come from the same Redux blob on the
individual job detail page (`/job/{id}`). No proxies needed — Cloudflare is
present but passive.

---

## Embed contents

Each post is a rich Discord embed with:

- Job/internship title linked to the listing
- Company name and logo thumbnail
- Location, work type / work mode, classification / field
- Salary (when disclosed)
- Posting / open / close dates (with relative labels: "today", "in 5 days", "closed")
- Source-specific fields (vacancies, employer rating, study fields for Prosple; bullet highlights for JobStreet)

In bot mode, each listing has buttons:

| Button | Prosple | Indeed | JobStreet |
|--------|---------|--------|-----------|
| Apply on company site | ✓ (when external URL available) | ✓ (when external ATS URL available) | — |
| View on [source] | ✓ | ✓ | ✓ |
| 📋 Job Description | ✓ ephemeral | ✓ ephemeral | ✓ ephemeral |

---

## Setup

### 1. Create the Discord bot

Go to <https://discord.com/developers/applications> → **New Application**.
Open the **Bot** tab → **Reset Token** → copy the token (keep it secret — it
goes in `.env` which is git-ignored). No privileged intents are needed.

### 2. Invite Sorpple to your server

**OAuth2 → URL Generator** → tick scope `bot`, then permissions:
**View Channel**, **Send Messages**, **Embed Links**
(also **Mention Everyone** if you want `@here`/`@everyone` pings).
Open the generated URL and add the bot to your server.

### 3. Get the channel ID

**User Settings → Advanced → Developer Mode** (on).
Right-click the target channel → **Copy Channel ID**.

### 4. Configure `.env`

Copy `.env.example` to `.env` and fill it in:

```env
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_CHANNEL_ID=123456789012345678
DISCORD_PING=@here                  # mention on NEW listings (blank = none)
POLL_INTERVAL_SECONDS=300           # poll frequency in seconds (default 5 min)
INIT_COUNT=10                       # listings to seed on first run
FETCH_LIMIT=30                      # listings to pull each poll

# Prosple only
INCLUDE_DESCRIPTION=true            # include full JD spoiler (monitor only)

# Indeed — proxies strongly recommended
INDEED_PROXIES_FILE=proxies.txt     # Wolveproxy format (host:port:user:pass)
INDEED_USE_PROXIES=true             # set false to disable

# JobStreet — no proxies needed
JOBSTREET_USE_PROXIES=false
```

To ping a role instead of `@here`, use `DISCORD_PING=<@&ROLE_ID>`.

### 5. Install the dependency

```powershell
python -m pip install -r requirements.txt   # installs discord.py (bot scripts only)
```

---

## Running Sorpple

Run all three bots together (one terminal each, or as background services):

```powershell
python prosple_bot.py
python indeed_bot.py
python jobstreet_bot.py
```

Each bot logs in, polls on its configured interval, and stays connected so the
**📋 Job Description** buttons keep working across restarts.

Test buttons without waiting for a new listing:

```powershell
python prosple_bot.py --sample
python indeed_bot.py --sample
python jobstreet_bot.py --sample
```

Each `--sample` command posts one listing immediately, then idles. Click the
📋 button — the description appears only to you (ephemeral).

### Running the zero-dependency monitors instead

Each monitor auto-detects mode from `.env`: bot (token + channel set) or webhook
(`DISCORD_WEBHOOK_URL`). Supports `--once` for Task Scheduler / cron:

```powershell
python prosple_monitor.py --once
python indeed_monitor.py --once
python jobstreet_monitor.py --once
```

---

## State files

Each source tracks its own seen listings independently:

| File | Source |
|------|--------|
| `state.json` | Prosple |
| `indeed_state.json` | Indeed |
| `jobstreet_state.json` | JobStreet |

All three are git-ignored and auto-created on first run. To re-seed a source
from scratch, delete its state file and run again.

---

## Repository structure

```
sorpple/
├── prosple_bot.py        # Prosple — interactive bot (buttons)
├── prosple_monitor.py    # Prosple — zero-dependency monitor (webhook or bot)
├── indeed_bot.py         # Indeed  — interactive bot (buttons)
├── indeed_monitor.py     # Indeed  — monitor + scraping core
├── jobstreet_bot.py      # JobStreet — interactive bot (buttons)
├── jobstreet_monitor.py  # JobStreet — monitor + scraping core
├── requirements.txt      # discord.py (bot scripts only)
├── .env.example          # configuration template
├── .env                  # your config (git-ignored)
├── proxies.txt           # Wolveproxy list for Indeed (git-ignored)
├── state.json            # Prosple seen-listings (auto-created, git-ignored)
├── indeed_state.json     # Indeed seen-listings (auto-created, git-ignored)
└── jobstreet_state.json  # JobStreet seen-listings (auto-created, git-ignored)
```
