# Sorpple

**Sorpple** is a Discord bot that monitors multiple Philippine job boards for
new internship listings and posts rich embeds to a Discord channel. On the first
run it seeds the channel with the latest listings; from then on it watches for
brand-new ones, pinging a year role when a listing mentions that intake year.

It is controlled from Discord itself — slash commands change poll intervals,
pause a source, or force a poll, with no restart and no `.env` edit.

Three sources are monitored in parallel:

| Source | URL monitored | Bot script | Monitor script |
|--------|--------------|------------|----------------|
| **Prosple PH** | ph.prosple.com | `prosple_bot.py` | `prosple_monitor.py` |
| **Indeed PH** | ph.indeed.com — intern jobs, Philippines, sorted by date | `indeed_bot.py` | `indeed_monitor.py` |
| **JobStreet PH** | ph.jobstreet.com — "intern" jobs, Philippines, sorted by date | `jobstreet_bot.py` | `jobstreet_monitor.py` |

Each source has two scripts: a **bot** (interactive, with buttons) and a
**monitor** (zero extra dependencies, webhook or basic bot, supports `--once`).

---

## Poll intervals

`sorpple.py` polls each source on its own schedule. Indeed is deliberately the
slowest: every Indeed request goes through a **paid rotating proxy** to get past
Cloudflare, while Prosple (GraphQL API) and JobStreet (SSR HTML) cost nothing.

| Source | Default | Env var | Why |
|--------|---------|---------|-----|
| Prosple | 10 min | `PROSPLE_POLL_MINUTES` | free API |
| JobStreet | 10 min | `JOBSTREET_POLL_MINUTES` | free HTML |
| Indeed | **30 min** | `INDEED_POLL_MINUTES` | proxy cost per request |

The env vars are only the *starting* values. `/interval` changes them live and
saves the new value to `sorpple_settings.json`, which then takes precedence:

**`sorpple_settings.json` > env var > built-in default**

> `POLL_INTERVAL_SECONDS` no longer affects `sorpple.py` — it now applies only to
> the standalone `*_bot.py` / `*_monitor.py` scripts.

---

## Discord commands

All commands require the **Manage Server** permission and reply privately
(ephemeral). `source` accepts `prosple`, `jobstreet`, `indeed`, or `all`.

| Command | What it does |
|---------|--------------|
| `/status` | Per source: interval, running/paused, next run, last poll, how many were new last cycle, seen-ID count, last error. Also lists the configured year pings. |
| `/interval <source> <minutes>` | Change a poll interval (1–1440) live and persist it. Applies to the sleep already in flight, so it does **not** trigger an extra poll. Warns if Indeed is set below 15 min. |
| `/pause <source>` | Stop polling a source. Survives restarts. |
| `/resume <source>` | Start it again — note this polls **once immediately**. |
| `/poll <source>` | Run one cycle right now without touching the timer. |

> **The bot must be invited with the `applications.commands` scope**, or the
> slash commands never appear no matter what the bot logs. See setup step 2.
>
> Set `DISCORD_GUILD_ID` for instant command registration. Without it commands
> sync globally and can take up to an hour to show up.

---

## Year pings (`@2027Start`)

When a new listing's text mentions a year you've configured a role for, Sorpple
pings that role:

```
<@&2027StartRoleId> 🎓 **[2027]** 🆕 **New internship** at Acme: Software Intern
```

Configure it in `.env` — the year in the variable name is what's matched, so
adding 2028 later needs no code change:

```env
PING_ROLE_2027=123456789012345678     # bare role id, or <@&123456789012345678>
```

Notes:

- **Listings with no year match post with no mention at all.** Sorpple pings year
  roles and nothing else — `DISCORD_PING` (`@here`) is not applied by `sorpple.py`.
- The role must be **mentionable**, or the bot needs **Mention Everyone** — Discord
  silently drops the ping otherwise.
- What gets scanned, per source — chosen so Indeed spends **no extra proxy requests**:

  | Source | Scanned text | Extra requests per new listing |
  |--------|--------------|-------------------------------|
  | Prosple | title, employer, summary, start date, **full job description** | 1 (free API) |
  | JobStreet | title, company, teaser, bullets, **full job description** | 1 (free HTML) |
  | Indeed | title, company, search snippet | **0** |

- First-run seeding is never scanned and never pings, so a fresh state file costs
  no extra requests.

Preview what would ping, without posting anything:

```powershell
python sorpple.py --check-2027        # newest 5 listings per source
python sorpple.py --check-2027 30     # the whole board — slower, finds more
```

Test that the mention actually pings — posts **one** message and exits, without
running a poll cycle or touching any state file:

```powershell
python sorpple.py --ping-test         # uses a real 2027 listing if one exists
```

It first checks whether the role is mentionable (or the bot has Mention
Everyone) and warns if Discord would swallow the ping.

---

## How each source works

### Prosple
Calls Prosple's GraphQL search API (persisted query
`OpportunitiesSearchWithoutStudyFieldFacetsModernLocations`), filtered to
internships in the Philippines, sorted newest-first. Full job descriptions come
from the `GetOpportunitySearchJobDetails` query. No proxies needed.

> **If Prosple starts returning `PersistedQueryNotFound`**, the Apollo query
> hashes have rotated — Prosple redeployed their front end. Recapture them from
> a HAR of ph.prosple.com and replace the two constants at the top of
> [prosple_monitor.py](prosple_monitor.py); the steps are in a comment right
> there. The `variables` we send survive a rotation, so nothing else changes.

### Indeed
Fetches `ph.indeed.com/jobs?q=intern&l=Philippines&sort=date` as HTML and
extracts job data from the `window.mosaic.providerData["mosaic-provider-jobcards"]`
JSON blob embedded in the page. Job descriptions are fetched by reloading the
same search URL with `?vjk={jobKey}` so Indeed renders the full detail panel
server-side — avoiding the separately Cloudflare-protected `/viewjob` endpoint.
**Proxies are strongly recommended** due to Cloudflare bot protection.

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

**OAuth2 → URL Generator** → tick scopes `bot` **and `applications.commands`**,
then permissions: **View Channel**, **Send Messages**, **Embed Links**
(also **Mention Everyone** if your year role isn't mentionable).
Open the generated URL and add the bot to your server.

> `applications.commands` is what makes `/status`, `/interval` and friends
> exist. If you invited the bot before, re-run this URL to add the scope —
> re-inviting an already-present bot just grants the missing scope.

### 3. Get the channel and server IDs

**User Settings → Advanced → Developer Mode** (on).
Right-click the target channel → **Copy Channel ID**.
Right-click the server icon → **Copy Server ID** (for `DISCORD_GUILD_ID`).

### 4. Configure `.env`

Copy `.env.example` to `.env` and fill it in:

```env
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_CHANNEL_ID=123456789012345678
DISCORD_GUILD_ID=123456789012345678  # instant slash-command registration

INIT_COUNT=10                        # listings to seed on first run
FETCH_LIMIT=30                       # listings to pull each poll

# Per-source poll intervals (sorpple.py)
PROSPLE_POLL_MINUTES=10
JOBSTREET_POLL_MINUTES=10
INDEED_POLL_MINUTES=30               # proxy-costly — keep this high

# Ping @2027Start when a listing mentions 2027
PING_ROLE_2027=123456789012345678

# Indeed — proxies strongly recommended
INDEED_PROXIES_FILE=proxies.txt      # proxy list (host:port:user:pass, one per line)
INDEED_USE_PROXIES=true              # set false to disable

# JobStreet — no proxies needed
JOBSTREET_USE_PROXIES=false

# Legacy — standalone *_bot.py / *_monitor.py scripts only
DISCORD_PING=@here
POLL_INTERVAL_SECONDS=300
INCLUDE_DESCRIPTION=true             # Prosple monitor: full JD spoiler
```

### 5. Install the dependency

```powershell
python -m pip install -r requirements.txt   # installs discord.py (bot scripts only)
```

---

## Running Sorpple

The recommended way is `sorpple.py` — a single process that runs all three
sources as one Discord gateway connection:

```powershell
python sorpple.py
```

Test all three sources and their buttons at once:

```powershell
python sorpple.py --sample   # posts one listing from each source then idles
```

Click the 📋 button on any card — the description appears only to you (ephemeral).

### Running sources individually

Each bot can also run standalone if needed:

```powershell
python prosple_bot.py
python indeed_bot.py
python jobstreet_bot.py
```

Or as one-shot monitor cycles (no `discord.py` required):

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
from scratch, delete its state file and restart.

`sorpple_settings.json` (also git-ignored, auto-created) holds the intervals and
paused flags set by the slash commands, so they survive restarts. Delete it to
fall back to the `.env` values.

---

## Repository structure

```
sorpple/
├── sorpple.py            # Unified bot — runs all three sources (recommended)
├── sorpple_commands.py   # Slash commands (/status, /interval, /pause, …)
├── prosple_bot.py        # Prosple — interactive bot (buttons)
├── prosple_monitor.py    # Prosple — zero-dependency monitor (webhook or bot)
├── indeed_bot.py         # Indeed  — interactive bot (buttons)
├── indeed_monitor.py     # Indeed  — monitor + scraping core
├── jobstreet_bot.py      # JobStreet — interactive bot (buttons)
├── jobstreet_monitor.py  # JobStreet — monitor + scraping core
├── requirements.txt      # discord.py (bot scripts only)
├── .env.example          # configuration template
├── .env                  # your config (git-ignored)
├── proxies.txt           # proxy list for Indeed (git-ignored)
├── state.json            # Prosple seen-listings (auto-created, git-ignored)
├── indeed_state.json     # Indeed seen-listings (auto-created, git-ignored)
├── jobstreet_state.json  # JobStreet seen-listings (auto-created, git-ignored)
└── sorpple_settings.json # Intervals / paused flags set via slash commands
```
