# Prosple PH Internship Monitor

Watches [ph.prosple.com](https://ph.prosple.com) for the newest **internships**
("Internship, Clerkship or Placement") and posts them to a Discord channel.
On the first run it seeds the channel with the 10 latest internships, then it
pings whenever a brand-new listing appears.

It works in either of two modes, picked automatically from your `.env`:

- **Bot mode** (recommended) — set `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID`.
  Posts via the bot API and includes real clickable **Apply buttons**.
- **Webhook mode** — set `DISCORD_WEBHOOK_URL`. Webhooks can't carry buttons, so
  the apply action is a clickable link inside the embed instead.

Each post is a rich embed showing all the relevant details: employer (with logo),
opportunity type, location, work mode, salary, vacancies, start date, application
open/close dates, employer rating, and relevant study fields — with the title
linked straight to the Prosple listing.

It also includes:

- **A collapsible full job description.** Discord has no real accordion, so the
  description is placed behind a spoiler (`||…||`) — blurred until you click to
  reveal it. The original HTML is converted to clean Discord markdown.
- **An apply button / link.** When Prosple exposes the employer's own
  application URL (`applyByUrl`), it points **straight to the company's careers
  site / ATS** (Workday, Lever, Greenhouse, Oracle, etc.) — skipping Prosple's
  sign-up wall. When the employer only accepts applications through Prosple, it
  points to the Prosple page instead (there's no external URL to skip).
  In bot mode this is a real button; in webhook mode it's a clickable link.

No third-party packages required — pure Python standard library.

## Setup (bot mode — with Apply buttons)

1. **Create the app & bot:** <https://discord.com/developers/applications> →
   **New Application**. Open the **Bot** tab → **Reset Token** → copy the token.
   (Keep it secret — it goes in `.env`, which is git-ignored.)
   No privileged intents are needed; the bot only *sends* messages.

2. **Invite the bot to your server:** **OAuth2 → URL Generator** → tick scope
   `bot`, then permissions **Send Messages** and **Embed Links** (also
   **Mention Everyone** if you want `@here`/`@everyone` pings). Open the
   generated URL and add it to your server.

3. **Get the channel id:** Discord → **User Settings → Advanced → Developer
   Mode** (on). Right-click the target channel → **Copy Channel ID**.

4. **Fill in `.env`** (copy from `.env.example`):

   ```
   DISCORD_BOT_TOKEN=your-bot-token
   DISCORD_CHANNEL_ID=123456789012345678
   DISCORD_PING=@here          # what to mention on a NEW listing (blank = none)
   POLL_INTERVAL_SECONDS=300   # how often to check (default 5 min)
   INIT_COUNT=10               # how many to seed on first run
   FETCH_LIMIT=30              # how many to pull each poll
   INCLUDE_DESCRIPTION=true    # embed the full job description (spoiler)
   ```

5. Run it.

## Setup (webhook mode — links instead of buttons)

Leave `DISCORD_BOT_TOKEN` / `DISCORD_CHANNEL_ID` blank and set
`DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxxx/yyyy` (Channel
Settings → Integrations → Webhooks). Everything else is the same.

To ping a specific role instead of `@here`, use `DISCORD_PING=<@&ROLE_ID>`.

## Running

**Continuously (recommended):**

```powershell
python prosple_monitor.py
```

It runs forever, polling every `POLL_INTERVAL_SECONDS`. Press `Ctrl+C` to stop.

**Single check (for Task Scheduler / cron):**

```powershell
python prosple_monitor.py --once
```

Does exactly one poll cycle and exits. Schedule this every few minutes with
Windows Task Scheduler if you'd rather not keep a process running.

## How it works

- Calls Prosple's GraphQL search API (persisted query
  `OpportunitiesSearchWithoutStudyFieldFacetsModernLocations`), filtered to
  internships in the Philippines, sorted newest-first.
- Tracks which listings it has already posted in `state.json` (ignored by git).
- **First run:** posts the latest `INIT_COUNT` (no ping) and records *every*
  currently-listed internship as "seen", so pre-existing ones are never
  mistaken for new arrivals.
- **Later runs:** any listing id not seen before is posted with the ping.

To re-seed from scratch, delete `state.json` and run again.

## Files

| File                 | Purpose                                      |
|----------------------|----------------------------------------------|
| `prosple_monitor.py` | The monitor.                                 |
| `.env`               | Your config (not committed).                 |
| `state.json`         | Seen-listing tracker (auto-created, ignored).|
