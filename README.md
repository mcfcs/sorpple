# Prosple PH Internship Monitor

Watches [ph.prosple.com](https://ph.prosple.com) for the newest **internships**
("Internship, Clerkship or Placement") and posts them to a Discord channel via a
webhook. On the first run it seeds the channel with the 10 latest internships,
then it pings whenever a brand-new listing appears.

Each post is a rich embed showing all the relevant details: employer (with logo),
opportunity type, location, work mode, salary, vacancies, start date, application
open/close dates, employer rating, and relevant study fields — with the title
linked straight to the Prosple listing.

It also includes:

- **A collapsible full job description.** Discord has no real accordion, so the
  description is placed behind a spoiler (`||…||`) — blurred until you click to
  reveal it. The original HTML is converted to clean Discord markdown.
- **An apply link.** When Prosple exposes the employer's own application URL
  (`applyByUrl`), the post links **straight to the company's careers site / ATS**
  (Workday, Lever, Greenhouse, Oracle, etc.) — skipping Prosple's sign-up wall.
  When the employer only accepts applications through Prosple, it links to the
  Prosple page instead (there's no external URL to skip in that case).

> Note: standard Discord *channel webhooks* cannot render real interactive
> buttons (Discord strips them) — that requires a bot/application. So the apply
> action is a prominent clickable markdown link instead.

No third-party packages required — pure Python standard library.

## Setup

1. Copy `.env.example` to `.env` and fill in your Discord webhook URL:

   ```
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxxx/yyyy
   DISCORD_PING=@here          # what to mention on a NEW listing (blank = no ping)
   POLL_INTERVAL_SECONDS=300   # how often to check (default 5 min)
   INIT_COUNT=10               # how many to seed on first run
   FETCH_LIMIT=30              # how many to pull each poll
   INCLUDE_DESCRIPTION=true    # embed the full job description (spoiler)
   ```

   To ping a specific role instead of `@here`, use `DISCORD_PING=<@&ROLE_ID>`.

2. Run it.

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
