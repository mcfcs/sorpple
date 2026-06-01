# Prosple PH Internship Monitor

Watches [ph.prosple.com](https://ph.prosple.com) for the newest **internships**
("Internship, Clerkship or Placement") and posts them to a Discord channel.
On the first run it seeds the channel with the 10 latest internships, then it
pings whenever a brand-new listing appears.

Each post is a rich embed showing all the relevant details: employer (with logo),
opportunity type, location, work mode, salary, vacancies, start date, application
open/close dates, employer rating, and relevant study fields — with the title
linked straight to the Prosple listing — plus an **Apply** action that, when
Prosple exposes the employer's own application URL (`applyByUrl`), points
**straight to the company's careers site / ATS** (Workday, Lever, Greenhouse,
Oracle, etc.), skipping Prosple's sign-up wall. (When the employer only accepts
applications through Prosple, it points to the Prosple page — there's no external
URL to skip.)

## Two ways to run it

| Script | Needs | Job description | Apply | Run model |
|--------|-------|-----------------|-------|-----------|
| **`prosple_bot.py`** (recommended) | `discord.py`, a bot token | **📋 button → shown privately** to the clicker (ephemeral) | real buttons | must run **continuously** (listens for clicks) |
| **`prosple_monitor.py`** | nothing (stdlib only) | spoiler `\|\|click to reveal\|\|` in the embed | button (bot) or link (webhook) | continuous **or** `--once` (Task Scheduler) |

`prosple_bot.py` is the nicer experience: a clean **📋 Job Description** button
that opens the full description as a private message only the clicker sees — no
spoiler blur, no channel clutter. The trade-off is it must stay running to answer
clicks, so it can't be scheduled as a one-shot.

`prosple_monitor.py` is the zero-dependency version; it can also post through a
bot (real Apply/View buttons) or a plain webhook (clickable links), and the full
description rides along behind a spoiler.

---

## Common setup

1. **Create the app & bot:** <https://discord.com/developers/applications> →
   **New Application**. Open the **Bot** tab → **Reset Token** → copy the token.
   (Keep it secret — it goes in `.env`, which is git-ignored.)
   No privileged intents are needed.

2. **Invite the bot to your server:** **OAuth2 → URL Generator** → tick scope
   `bot`, then permissions **View Channel**, **Send Messages**, **Embed Links**
   (also **Mention Everyone** if you want `@here`/`@everyone` pings). Open the
   generated URL and add it to your server.

3. **Get the channel id:** Discord → **User Settings → Advanced → Developer
   Mode** (on). Right-click the target channel → **Copy Channel ID**.

4. **Copy `.env.example` to `.env`** and fill it in:

   ```
   DISCORD_BOT_TOKEN=your-bot-token
   DISCORD_CHANNEL_ID=123456789012345678
   DISCORD_PING=@here          # what to mention on a NEW listing (blank = none)
   POLL_INTERVAL_SECONDS=300   # how often to check (default 5 min)
   INIT_COUNT=10               # how many to seed on first run
   FETCH_LIMIT=30              # how many to pull each poll
   INCLUDE_DESCRIPTION=true    # (prosple_monitor.py only) full JD spoiler
   ```

   To ping a role instead of `@here`, use `DISCORD_PING=<@&ROLE_ID>`.

---

## Running the interactive bot (`prosple_bot.py`)

Install the one dependency, then run it:

```powershell
python -m pip install -r requirements.txt   # installs discord.py
python prosple_bot.py
```

It logs in, polls every `POLL_INTERVAL_SECONDS`, and stays connected so the
**📋 Job Description** buttons keep working (including on old messages, across
restarts). Press `Ctrl+C` to stop. Because it must be online to answer clicks,
it has no `--once` mode — keep it running (e.g. via NSSM / a service / a always-on
machine).

Test the button without waiting for a new listing:

```powershell
python prosple_bot.py --sample   # posts one sample listing, then idles
```

Click its **📋 Job Description** button — the description appears only to you.

## Running the zero-dependency monitor (`prosple_monitor.py`)

Auto-detects mode from `.env`: bot (token + channel set) or webhook
(`DISCORD_WEBHOOK_URL`). The apply action is a real button in bot mode, a
clickable link in webhook mode; the full description is a spoiler either way.

```powershell
python prosple_monitor.py          # run continuously (polls every 5 min)
python prosple_monitor.py --once   # one cycle then exit (for Task Scheduler/cron)
```

---

## How it works

- Calls Prosple's GraphQL search API (persisted query
  `OpportunitiesSearchWithoutStudyFieldFacetsModernLocations`), filtered to
  internships in the Philippines, sorted newest-first. The full description
  comes from `GetOpportunitySupplementaryDetails`.
- Tracks which listings it has already posted in `state.json` (ignored by git).
- **First run:** posts the latest `INIT_COUNT` (no ping) and records *every*
  currently-listed internship as "seen", so pre-existing ones are never
  mistaken for new arrivals.
- **Later runs:** any listing id not seen before is posted with the ping.

Both scripts share `state.json`, so they won't double-post. To re-seed the
channel from scratch, delete `state.json` and run again.

## Files

| File                 | Purpose                                              |
|----------------------|------------------------------------------------------|
| `prosple_bot.py`     | Interactive bot — ephemeral 📋 Job Description button.|
| `prosple_monitor.py` | Zero-dependency monitor — webhook or basic bot.      |
| `requirements.txt`   | `discord.py` (only needed for `prosple_bot.py`).     |
| `.env`               | Your config (not committed).                         |
| `state.json`         | Seen-listing tracker (auto-created, ignored).        |
