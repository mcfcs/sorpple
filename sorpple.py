"""
Sorpple — unified Discord bot for Prosple PH, Indeed PH, and JobStreet PH.

Runs all three internship monitors as a single Discord gateway connection with
three independent polling tasks and all button handlers registered in one client.
This is the recommended way to run Sorpple.

Each source polls on its own interval — Indeed is proxy-costly, so it defaults to
30 minutes while the two free sources default to 10.  Intervals can be changed
live from Discord (/interval) and are persisted to sorpple_settings.json.

New listings whose text mentions a watched year (2027 by default) ping the role
configured for that year, e.g. PING_ROLE_2027=<@&...> for @2027Start.  Listings
with no year hit post with no mention at all.

Run:  python sorpple.py
      python sorpple.py --sample         (post one listing from each source then idle)
      python sorpple.py --check-2027 [N] (scan the newest N listings per source and
                                          print which would ping; posts nothing)
      python sorpple.py --ping-test [YR] (post ONE listing with the year role ping to
                                          verify the mention works, then exit)
      python sorpple.py --backfill [N]   (archive the newest N listings per source for
                                          the web dashboard; posts nothing)

While running, the bot also serves the web dashboard: it drains actions queued by
sorpple_web.py and publishes live telemetry back.  See sorpple_control.py.
"""

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import discord
from discord import app_commands
from discord.ext import tasks

# ── Monitor modules (data + embed layer) ──────────────────────────────────────
from prosple_monitor import (
    SCRIPT_DIR,
    _safe,
    build_embed as prosple_build_embed,
    fetch_description as prosple_fetch_description,
    fetch_internships,
    html_to_markdown as prosple_html_to_markdown,
    load_dotenv,
    load_state as prosple_load_state,
    log,
    save_state as prosple_save_state,
)
from indeed_monitor import load_proxies
from indeed_monitor import (
    build_embed as indeed_build_embed,
    fetch_jobs as indeed_fetch_jobs,
    html_to_markdown as indeed_html_to_markdown,
    load_state as indeed_load_state,
    save_state as indeed_save_state,
)
from jobstreet_monitor import (
    build_embed as js_build_embed,
    fetch_description as js_fetch_description,
    fetch_jobs as js_fetch_jobs,
    html_to_markdown as js_html_to_markdown,
    load_state as js_load_state,
    save_state as js_save_state,
)

# ── Bot modules — import for their DynamicItem buttons and build_view helpers.
# _PROXIES globals in each are set in load_config() so button callbacks
# (which reference the module-level list) have proxy access.
import indeed_bot as _ib
import jobstreet_bot as _jb
from prosple_bot import JobDescriptionButton as ProspleJDButton
from prosple_bot import build_view as prosple_build_view
from indeed_bot import JobDescriptionButton as IndeedJDButton
from indeed_bot import build_view as indeed_build_view
from jobstreet_bot import JobDescriptionButton as JSJDButton
from jobstreet_bot import build_view as js_build_view

import sorpple_commands
import sorpple_archive
import sorpple_control

SETTINGS_FILE = os.path.join(SCRIPT_DIR, "sorpple_settings.json")

# How often the bot drains web-dashboard actions and republishes its telemetry.
# Short enough that a click feels responsive, long enough to be free.
CONTROL_TICK_SECONDS = 2

# Built-in per-source poll intervals, in minutes.  Indeed is deliberately the
# slowest: every Indeed request goes through a paid rotating proxy to get past
# Cloudflare, while Prosple (GraphQL API) and JobStreet (SSR HTML) are free.
DEFAULT_MINUTES = {"prosple": 10, "jobstreet": 10, "indeed": 30}

MIN_MINUTES = 1
MAX_MINUTES = 1440

# A standalone 4-digit 20xx year.  The lookarounds reject digits glued to either
# side, so "12027" and "20275" never match while "Class of 2027", "Jan 2027" and
# "S.Y. 2026-2027" all do.
_YEAR_RE = re.compile(r"(?<!\d)(20[2-9]\d)(?!\d)")

# Env vars naming the role to ping for a given year: PING_ROLE_2027=<@&id>
_PING_ROLE_ENV_RE = re.compile(r"^PING_ROLE_(20[2-9]\d)$")


def mentioned_years(text: str) -> set[str]:
    """Return the set of standalone 20xx years appearing in `text`."""
    return set(_YEAR_RE.findall(text or ""))


def _role_mention(raw: str) -> str | None:
    """Normalize a role config value into a `<@&id>` mention.

    Accepts either a bare role id ("123456789012345678") or an already-formatted
    mention ("<@&123456789012345678>").  Returns None if neither.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    m = re.fullmatch(r"<@&(\d+)>", raw)
    if m:
        return f"<@&{m.group(1)}>"
    if raw.isdigit():
        return f"<@&{raw}>"
    log(f"WARNING: ignoring unrecognised role value {raw!r} (expected a role id or <@&id>).")
    return None


def load_year_roles() -> dict[str, str]:
    """Collect every PING_ROLE_<YEAR> env var into {year: '<@&id>'}."""
    roles = {}
    for name, value in os.environ.items():
        m = _PING_ROLE_ENV_RE.match(name)
        if not m:
            continue
        mention = _role_mention(value)
        if mention:
            roles[m.group(1)] = mention
    return roles


# ── Runtime settings (survive restarts; written by the slash commands) ────────

def load_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return {"intervals": {}, "paused": {}}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("intervals", {})
        data.setdefault("paused", {})
        return data
    except (json.JSONDecodeError, OSError) as exc:
        log(f"WARNING: could not read {os.path.basename(SETTINGS_FILE)} ({exc}); using defaults.")
        return {"intervals": {}, "paused": {}}


def save_settings(settings: dict) -> None:
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2)
    os.replace(tmp, SETTINGS_FILE)


# ─────────────────────────────────────────────────────────────────────────────
# Source registry — one description per job board, so the polling logic below
# is written once instead of three times.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Source:
    key: str                 # "prosple" | "indeed" | "jobstreet"
    label: str               # "Prosple"
    fetch: Callable          # (limit) -> list[dict]        (blocking)
    embed: Callable          # (item)  -> dict
    view: Callable           # (item)  -> discord.ui.View
    load_state: Callable     # ()      -> dict
    save_state: Callable     # (state) -> None
    describe: Callable       # (item)  -> "at Acme: Software Intern"
    scan_text: Callable      # (item)  -> str  (blocking; may fetch the full JD)
    default_minutes: int

    loop: tasks.Loop | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # Live telemetry for /status.
    minutes: int = 0
    paused: bool = False
    last_poll: datetime | None = None
    last_new: int = 0
    last_error: str | None = None


# ── Per-source describe() helpers ─────────────────────────────────────────────

def _prosple_describe(opp: dict) -> str:
    employer = _safe(opp, "parentEmployer", "title") or "an employer"
    return f"at {employer}: {opp.get('title', 'internship')}"


def _job_describe(job: dict) -> str:
    return f"at {job['company']}: {job['title']}"


# ── Per-source scan_text() helpers ────────────────────────────────────────────
# These run in a worker thread (never on the event loop).  Prosple and JobStreet
# pull the full job description because both endpoints are free; Indeed sticks to
# what the search response already returned so no extra proxy request is spent.

def _prosple_scan_text(opp: dict) -> str:
    parts = [
        opp.get("title") or "",
        _safe(opp, "parentEmployer", "title") or "",
        _safe(opp, "overview", "summary") or "",
        _safe(opp, "startDate", "category", "label") or "",
    ]
    raw = prosple_fetch_description(opp.get("id"))
    if raw:
        parts.append(prosple_html_to_markdown(raw))
    return "\n".join(parts)


def _js_scan_text(job: dict) -> str:
    parts = [job.get("title") or "", job.get("company") or "", job.get("teaser") or ""]
    parts.extend(job.get("bullet_points") or [])
    raw = js_fetch_description(job["id"], _jb._PROXIES or None)
    if raw:
        parts.append(js_html_to_markdown(raw))
    return "\n".join(parts)


def _indeed_scan_text(job: dict) -> str:
    # Search-response fields only — no viewjob fetch, no proxy spend.
    return "\n".join([
        job.get("title") or "",
        job.get("company") or "",
        indeed_html_to_markdown(job.get("snippet_html") or ""),
    ])


def build_sources(fetch_limit: int) -> dict[str, Source]:
    return {
        "prosple": Source(
            key="prosple",
            label="Prosple",
            fetch=lambda: fetch_internships(fetch_limit),
            embed=lambda o: prosple_build_embed(
                o, include_full_description=False, include_apply_links=False
            ),
            view=prosple_build_view,
            load_state=prosple_load_state,
            save_state=prosple_save_state,
            describe=_prosple_describe,
            scan_text=_prosple_scan_text,
            default_minutes=DEFAULT_MINUTES["prosple"],
        ),
        "jobstreet": Source(
            key="jobstreet",
            label="JobStreet",
            fetch=lambda: js_fetch_jobs(fetch_limit, _jb._PROXIES or None),
            embed=lambda j: js_build_embed(j, include_teaser=False, include_apply_links=False),
            view=js_build_view,
            load_state=js_load_state,
            save_state=js_save_state,
            describe=_job_describe,
            scan_text=_js_scan_text,
            default_minutes=DEFAULT_MINUTES["jobstreet"],
        ),
        "indeed": Source(
            key="indeed",
            label="Indeed",
            fetch=lambda: indeed_fetch_jobs(fetch_limit, _ib._PROXIES or None),
            embed=lambda j: indeed_build_embed(
                j, include_snippet=False, include_apply_links=False
            ),
            view=indeed_build_view,
            load_state=indeed_load_state,
            save_state=indeed_save_state,
            describe=_job_describe,
            scan_text=_indeed_scan_text,
            default_minutes=DEFAULT_MINUTES["indeed"],
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────

class SorppleBot(discord.Client):
    def __init__(self, config: dict, sample_mode: bool = False,
                 ping_test_year: str | None = None):
        super().__init__(intents=discord.Intents.default())
        self.config         = config
        self.sample_mode    = sample_mode
        self.ping_test_year = ping_test_year
        self.settings    = load_settings()
        self.sources     = build_sources(config["fetch_limit"])
        self.tree        = app_commands.CommandTree(self)

        # Effective interval / paused state: settings file > env > built-in default.
        # A hand-edited or truncated settings file must not stop the bot booting,
        # so anything unusable falls back to the env/default value.
        for key, src in self.sources.items():
            env_minutes = config["minutes"][key]
            try:
                minutes = int(self.settings["intervals"].get(key, env_minutes))
            except (TypeError, ValueError):
                log(f"[{src.label}] Bad saved interval; using {env_minutes} min.")
                minutes = env_minutes
            src.minutes = min(MAX_MINUTES, max(MIN_MINUTES, minutes))
            src.paused  = bool(self.settings["paused"].get(key, False))

        self.sources["prosple"].loop   = self.prosple_poller
        self.sources["jobstreet"].loop = self.js_poller
        self.sources["indeed"].loop    = self.indeed_poller

    # ── Settings persistence ──────────────────────────────────────────────────

    def persist_settings(self):
        self.settings["intervals"] = {k: s.minutes for k, s in self.sources.items()}
        self.settings["paused"]    = {k: s.paused for k, s in self.sources.items()}
        try:
            save_settings(self.settings)
        except OSError as exc:
            log(f"WARNING: could not write {os.path.basename(SETTINGS_FILE)}: {exc}")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup_hook(self):
        # Register all three button namespaces so old messages stay clickable.
        self.add_dynamic_items(ProspleJDButton)
        self.add_dynamic_items(IndeedJDButton)
        self.add_dynamic_items(JSJDButton)

        sorpple_commands.register(self)
        await self._sync_commands()

        # Both test modes post one message and stop; starting the pollers would
        # dump the whole backlog into the channel.
        if self.sample_mode or self.ping_test_year:
            return

        # Drain any actions queued while the bot was down, and start the heartbeat
        # so the dashboard shows the bot as online.
        self.control_watcher.start()

        for src in self.sources.values():
            src.loop.change_interval(minutes=src.minutes)
            if src.paused:
                log(f"[{src.label}] Paused (per saved settings); not starting poller.")
                continue
            src.loop.start()
            log(f"[{src.label}] Polling every {src.minutes} min.")

    async def _sync_commands(self):
        """Publish the slash commands.

        Guild-scoped sync is instant; a global sync can take up to an hour to
        appear in clients.  Set DISCORD_GUILD_ID to get the fast path.
        """
        guild_id = self.config.get("guild_id")
        if guild_id and not guild_id.isdigit():
            log(f"WARNING: DISCORD_GUILD_ID={guild_id!r} is not a server id; syncing globally.")
            guild_id = ""
        try:
            if guild_id:
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log(f"Synced {len(synced)} command(s) to guild {guild_id}.")
            else:
                synced = await self.tree.sync()
                log(
                    f"Synced {len(synced)} command(s) globally — these can take up to "
                    "an hour to appear.  Set DISCORD_GUILD_ID for instant registration."
                )
        except discord.HTTPException as exc:
            log(f"WARNING: could not sync slash commands: {exc!r}")

    async def on_ready(self):
        log(f"Sorpple logged in as {self.user} (id {self.user.id}).")
        if self.sample_mode:
            await self._post_samples()
        elif self.ping_test_year:
            try:
                await self._post_ping_test(self.ping_test_year)
            finally:
                await self.close()

    async def _channel(self):
        return await self.fetch_channel(int(self.config["channel_id"]))

    async def _send(self, channel, embed_dict: dict, view: discord.ui.View,
                    content: str | None = None):
        await channel.send(
            content=content,
            embed=discord.Embed.from_dict(embed_dict),
            view=view,
            allowed_mentions=discord.AllowedMentions(everyone=True, roles=True, users=True),
        )

    # ── Sample mode ───────────────────────────────────────────────────────────

    async def _post_samples(self):
        loop    = asyncio.get_running_loop()
        channel = await self._channel()
        note    = "**[SAMPLE]** Click 📋 Job Description — shows only to you. Safe to delete."

        for src in self.sources.values():
            try:
                items = await loop.run_in_executor(None, src.fetch)
                if items:
                    item = items[0]
                    await self._send(channel, src.embed(item), src.view(item),
                                     content=f"**[{src.label}]** {note}")
                    await asyncio.sleep(1)
            except Exception as exc:
                log(f"[{src.label}] Sample error: {exc!r}")

        log("Samples posted. Click the buttons to test; Ctrl+C to stop.")

    # ── Ping test ─────────────────────────────────────────────────────────────

    async def _post_ping_test(self, year: str):
        """Post exactly one listing as if it had matched `year`, then exit.

        This is a real ping — anyone holding the role is notified — and it goes
        through the same _content_for() path the poller uses, so it proves the
        mention actually resolves rather than just looking right in a log.  It
        deliberately does not run a poll cycle, so no backlog is posted and no
        state file is touched.
        """
        role = self.config["year_roles"].get(year)
        if not role:
            log(f"No PING_ROLE_{year} configured in .env — nothing to test.")
            return

        channel = await self._channel()

        # Diagnose the role before posting: Discord silently drops a mention for
        # a role that isn't mentionable unless the bot has Mention Everyone.
        role_id = int(re.fullmatch(r"<@&(\d+)>", role).group(1))
        guild   = getattr(channel, "guild", None)
        obj     = guild.get_role(role_id) if guild else None
        if obj is None:
            log(f"WARNING: role {role_id} not found in this server — the mention "
                f"will render as a dead @role and ping nobody.")
        else:
            # Not reporting a member count: without the privileged members
            # intent the member cache is mostly empty, so len(obj.members) would
            # read as "nobody has this role" even when people do.
            perms = channel.permissions_for(guild.me)
            log(f"Role check: @{obj.name} · mentionable={obj.mentionable} · "
                f"bot has mention_everyone={perms.mention_everyone}")
            if not obj.mentionable and not perms.mention_everyone:
                log("WARNING: role is not mentionable and the bot lacks Mention "
                    "Everyone — Discord will show the mention but ping nobody.")

        loop = asyncio.get_running_loop()
        fallback = None   # (source, item) to use if no listing genuinely matches

        for src in self.sources.values():
            try:
                items = await loop.run_in_executor(None, src.fetch)
            except Exception as exc:
                log(f"[{src.label}] Fetch failed: {exc!r}")
                continue
            if not items:
                continue
            if fallback is None:
                fallback = (src, items[0])

            # Prefer a listing that really does mention the year: that exercises
            # detection and the ping together, end to end.
            log(f"[{src.label}] Looking for a real {year} listing…")
            for item in items:
                if year in await self._year_hits(src, item):
                    await self._send(
                        channel, src.embed(item), src.view(item),
                        content=f"**[PING TEST]** {self._content_for(src, item, [year])}\n"
                                f"*Genuine {year} match, posted on demand. Safe to delete.*",
                    )
                    log(f"[{src.label}] Ping test posted with a REAL {year} match: "
                        f"{src.describe(item)}")
                    return

        if fallback is None:
            log("No source returned a listing — nothing posted.")
            return

        # Nothing on the boards mentions the year right now, so ping on the newest
        # listing instead — the mention is still the thing under test.
        src, item = fallback
        await self._send(
            channel, src.embed(item), src.view(item),
            content=f"**[PING TEST]** {self._content_for(src, item, [year])}\n"
                    f"*No listing currently mentions {year} — this is a forced ping "
                    f"on the newest listing. Safe to delete.*",
        )
        log(f"[{src.label}] No real {year} match on any board; posted a forced ping test.")

    # ── Year detection ────────────────────────────────────────────────────────

    async def _year_hits(self, src: Source, item: dict) -> list[str]:
        """Watched years mentioned by this listing, sorted ascending.

        Only years with a configured PING_ROLE_<YEAR> are considered — scanning
        for years nobody subscribed to would spend requests for nothing.
        """
        watched = self.config["year_roles"]
        if not watched:
            return []
        loop = asyncio.get_running_loop()
        try:
            text = await loop.run_in_executor(None, src.scan_text, item)
        except Exception as exc:  # noqa: BLE001 — a failed scan must not block the post
            log(f"[{src.label}] Year scan failed for {item.get('id')}: {exc!r}")
            return []
        return sorted(mentioned_years(text) & set(watched))

    def _content_for(self, src: Source, item: dict, years: list[str]) -> str:
        """Message text for a new listing.

        Only year roles are ever mentioned; a listing with no year hit posts with
        no mention at all.
        """
        content = f"🆕 **New internship** {src.describe(item)}"
        if not years:
            return content
        roles = " ".join(self.config["year_roles"][y] for y in years)
        tags  = " ".join(f"**[{y}]**" for y in years)
        return f"{roles} 🎓 {tags} {content}"

    # ── Shared poll cycle ─────────────────────────────────────────────────────

    async def run_source(self, key: str) -> tuple[int, str]:
        """One poll cycle for `key`.  Returns (listings_posted, human_summary).

        Serialized per source by a lock so an on-demand /poll can never race the
        scheduled loop into posting the same listing twice.
        """
        src = self.sources[key]
        async with src.lock:
            try:
                result = await self._poll(src)
                src.last_error = None
                return result
            except Exception as exc:  # noqa: BLE001 — keep the loop alive
                src.last_error = repr(exc)
                log(f"[{src.label}] Poll error: {exc!r}")
                return 0, f"error: {exc!r}"
            finally:
                src.last_poll = datetime.now(timezone.utc)

    async def _poll(self, src: Source) -> tuple[int, str]:
        loop  = asyncio.get_running_loop()
        items = await loop.run_in_executor(None, src.fetch)
        if not items:
            log(f"[{src.label}] No listings returned.")
            src.last_new = 0
            return 0, "no listings returned"

        channel = await self._channel()
        state   = src.load_state()
        seen    = set(state["seen_ids"])

        if not state["initialized"]:
            latest = items[: self.config["init_count"]]
            log(f"[{src.label}] Init: posting {len(latest)} listings.")
            # Seeding is silent and unscanned — no pings, and no per-listing
            # description fetches on a fresh state file.
            for item in reversed(latest):
                await self._send(channel, src.embed(item), src.view(item))
                await asyncio.sleep(1)
            # Archive the whole fetched page, not just the seeded slice: the rest
            # is marked seen below and would otherwise never reach the dashboard.
            await loop.run_in_executor(None, sorpple_archive.record_many, src.key, items)
            for item in items:
                seen.add(item["id"])
            state.update(initialized=True, seen_ids=sorted(seen))
            src.save_state(state)
            log(f"[{src.label}] Init complete.")
            src.last_new = len(latest)
            return len(latest), f"seeded {len(latest)} listing(s)"

        new = [i for i in items if i["id"] not in seen]
        src.last_new = len(new)
        if not new:
            log(f"[{src.label}] No new listings.")
            return 0, "no new listings"

        log(f"[{src.label}] {len(new)} new listing(s).")
        posted = 0
        pinged = 0
        for item in reversed(new):
            years = await self._year_hits(src, item)
            if years:
                pinged += 1
                log(f"[{src.label}] {item['id']} mentions {', '.join(years)} — pinging role.")
            await self._send(
                channel,
                src.embed(item),
                src.view(item),
                content=self._content_for(src, item, years),
            )
            # Keep the dashboard's copy in step with the channel.  Runs off the
            # event loop, and never raises — see sorpple_archive.record().
            await loop.run_in_executor(None, sorpple_archive.record, src.key, item, years)
            seen.add(item["id"])
            posted += 1
            await asyncio.sleep(1)

        state["seen_ids"] = sorted(seen)
        src.save_state(state)

        summary = f"posted {posted} new listing(s)"
        if pinged:
            summary += f", {pinged} with a year ping"
        return posted, summary

    # ── Pollers ───────────────────────────────────────────────────────────────
    # Intervals here are placeholders; setup_hook applies the effective value.

    @tasks.loop(minutes=DEFAULT_MINUTES["prosple"])
    async def prosple_poller(self):
        await self.run_source("prosple")

    @prosple_poller.before_loop
    async def _before_prosple(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=DEFAULT_MINUTES["jobstreet"])
    async def js_poller(self):
        await self.run_source("jobstreet")

    @js_poller.before_loop
    async def _before_js(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=DEFAULT_MINUTES["indeed"])
    async def indeed_poller(self):
        await self.run_source("indeed")

    @indeed_poller.before_loop
    async def _before_indeed(self):
        await self.wait_until_ready()

    # ── Web dashboard bridge ──────────────────────────────────────────────────
    # The dashboard runs in its own process, so it cannot touch these loop
    # objects directly.  It queues actions to a file; this task applies them and
    # publishes telemetry back.  Every action below routes through the same
    # mutation the matching slash command performs, so Discord and the website
    # can never drift apart.

    @tasks.loop(seconds=CONTROL_TICK_SECONDS)
    async def control_watcher(self):
        try:
            actions = await asyncio.get_running_loop().run_in_executor(
                None, sorpple_control.drain
            )
            for action in actions:
                await self._apply_control(action)
            sorpple_control.publish_status(self._status_payload())
        except Exception as exc:  # noqa: BLE001 — the bridge must not kill the bot
            log(f"[control] Tick failed: {exc!r}")

    @control_watcher.before_loop
    async def _before_control(self):
        await self.wait_until_ready()

    def _status_payload(self) -> dict:
        """Live telemetry for the dashboard — the /status embed as JSON."""
        sources = {}
        for key, src in self.sources.items():
            running = src.loop is not None and src.loop.is_running()
            next_at = src.loop.next_iteration if running else None
            sources[key] = {
                "key":        key,
                "label":      src.label,
                "minutes":    src.minutes,
                "running":    running,
                "paused":     src.paused,
                "next_run":   next_at.isoformat() if next_at else None,
                "last_poll":  src.last_poll.isoformat() if src.last_poll else None,
                "last_new":   src.last_new,
                "last_error": src.last_error,
                "seen_count": self._seen_count(src),
            }
        return {
            "online":     True,
            "bot_user":   str(self.user) if self.user else None,
            "sources":    sources,
            "year_roles": sorted(self.config.get("year_roles") or {}),
        }

    @staticmethod
    def _seen_count(src: Source) -> int:
        try:
            return len(src.load_state().get("seen_ids", []))
        except Exception:  # noqa: BLE001 — telemetry must survive a bad state file
            return 0

    async def _apply_control(self, action: dict) -> None:
        name   = action.get("action")
        target = action.get("source", "all")
        value  = action.get("value")

        if target == "all":
            targets = list(self.sources.values())
        elif target in self.sources:
            targets = [self.sources[target]]
        else:
            log(f"[control] Ignoring action for unknown source {target!r}.")
            return

        for src in targets:
            if name == "interval":
                try:
                    minutes = int(value)
                except (TypeError, ValueError):
                    log(f"[control] Ignoring non-numeric interval {value!r}.")
                    return
                src.minutes = min(MAX_MINUTES, max(MIN_MINUTES, minutes))
                # Recalculates the sleep already in flight, so no extra poll fires.
                src.loop.change_interval(minutes=src.minutes)
                log(f"[{src.label}] Interval set to {src.minutes} min from the dashboard.")

            elif name in ("pause", "monitor") and (name == "pause" or not value):
                src.paused = True
                if src.loop.is_running():
                    src.loop.cancel()
                log(f"[{src.label}] Paused from the dashboard.")

            elif name in ("resume", "monitor"):
                src.paused = False
                if not src.loop.is_running():
                    src.loop.change_interval(minutes=src.minutes)
                    src.loop.start()   # discord.py runs the body immediately
                log(f"[{src.label}] Resumed from the dashboard.")

            elif name == "poll":
                log(f"[{src.label}] Manual poll requested from the dashboard.")
                await self.run_source(src.key)

            else:
                log(f"[control] Ignoring unknown action {name!r}.")
                return

        self.persist_settings()


# ─────────────────────────────────────────────────────────────────────────────

def load_config(require_discord: bool = True) -> dict:
    load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

    token      = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if require_discord and (not token or not channel_id):
        log("ERROR: DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID must be set in .env.")
        sys.exit(1)

    def _int(name, default):
        try:
            return max(1, int(os.environ.get(name, default)))
        except ValueError:
            return default

    def _minutes(name, default):
        return min(MAX_MINUTES, max(MIN_MINUTES, _int(name, default)))

    # Indeed proxies — strongly recommended.
    indeed_file = os.environ.get(
        "INDEED_PROXIES_FILE", os.path.join(SCRIPT_DIR, "proxies.txt")
    )
    if os.environ.get("INDEED_USE_PROXIES", "").strip().lower() in ("0", "false", "no", "off"):
        _ib._PROXIES = []
        log("Indeed: proxies disabled.")
    else:
        _ib._PROXIES = load_proxies(indeed_file)
        log(f"Indeed: {len(_ib._PROXIES)} proxies loaded." if _ib._PROXIES
            else "Indeed: no proxy file found; direct requests.")

    # JobStreet proxies — not needed; off by default unless explicitly enabled.
    js_file = os.environ.get(
        "JOBSTREET_PROXIES_FILE", os.path.join(SCRIPT_DIR, "proxies.txt")
    )
    if os.environ.get("JOBSTREET_USE_PROXIES", "").strip().lower() in ("1", "true", "yes", "on"):
        _jb._PROXIES = load_proxies(js_file)
        log(f"JobStreet: {len(_jb._PROXIES)} proxies loaded.")
    else:
        _jb._PROXIES = []

    year_roles = load_year_roles()
    if year_roles:
        log("Year pings: " + ", ".join(f"{y} → {r}" for y, r in sorted(year_roles.items())))
    else:
        log("Year pings: none configured (set PING_ROLE_2027 to enable the @2027Start ping).")

    return {
        "token":       token,
        "channel_id":  channel_id,
        "guild_id":    os.environ.get("DISCORD_GUILD_ID", "").strip(),
        "year_roles":  year_roles,
        "minutes": {
            "prosple":   _minutes("PROSPLE_POLL_MINUTES",   DEFAULT_MINUTES["prosple"]),
            "jobstreet": _minutes("JOBSTREET_POLL_MINUTES", DEFAULT_MINUTES["jobstreet"]),
            "indeed":    _minutes("INDEED_POLL_MINUTES",    DEFAULT_MINUTES["indeed"]),
        },
        "init_count":  _int("INIT_COUNT", 10),
        "fetch_limit": _int("FETCH_LIMIT", 30),
    }


def check_years(config: dict, scan_limit: int = 5) -> None:
    """Fetch current listings from every source, run the real scan, post nothing.

    Uses the same scan_text() callables as the live bot, so Indeed still makes no
    per-listing description request here.  Only the newest `scan_limit` listings
    per source are scanned — Prosple and JobStreet fetch one description each, so
    scanning a full board of 30 would mean 60 sequential requests.
    """
    watched = set(config["year_roles"]) or {"2027"}
    log(f"Dry-run year scan (watching {', '.join(sorted(watched))}); nothing will be posted.")

    for src in build_sources(config["fetch_limit"]).values():
        try:
            items = src.fetch()
        except Exception as exc:  # noqa: BLE001
            log(f"[{src.label}] Fetch failed: {exc!r}")
            continue
        items = items[:scan_limit]
        log(f"[{src.label}] Scanning newest {len(items)} listing(s)…")
        for item in items:
            try:
                hits = sorted(mentioned_years(src.scan_text(item)) & watched)
            except Exception as exc:  # noqa: BLE001
                log(f"[{src.label}]   {item['id']}: scan failed ({exc!r})")
                continue
            if hits:
                role = " ".join(config["year_roles"].get(y, "(no role set)") for y in hits)
                log(f"[{src.label}]   HIT {', '.join(hits)} → {role} — {src.describe(item)}")
            else:
                log(f"[{src.label}]   --  {src.describe(item)}")
        log(f"[{src.label}] Done.")


def backfill(config: dict, limit: int | None = None) -> None:
    """Archive each board's current page without posting anything to Discord.

    The state files only ever stored ids, so listings already posted have no
    content to recover.  This gives the dashboard a real corpus immediately
    instead of leaving it empty until the next new listing appears.

    Nothing is sent to Discord and no state file is touched, so this is safe to
    run against a live bot.
    """
    count = limit or config["fetch_limit"]
    log(f"Backfilling the archive with the newest {count} listing(s) per source.")
    total = 0

    for src in build_sources(count).values():
        try:
            items = src.fetch()
        except Exception as exc:  # noqa: BLE001
            log(f"[{src.label}] Fetch failed: {exc!r}")
            continue
        stored = sorpple_archive.record_many(src.key, items)
        total += stored
        log(f"[{src.label}] Archived {stored} listing(s).")

    log(f"Backfill complete — {total} listing(s) in "
        f"{os.path.basename(sorpple_archive.ARCHIVE_FILE)}.")


def _flag_value(flag: str, default: str) -> str:
    """Read the optional value after a flag (e.g. `--check-2027 10`)."""
    idx = sys.argv.index(flag)
    if idx + 1 < len(sys.argv) and sys.argv[idx + 1].isdigit():
        return sys.argv[idx + 1]
    return default


def main():
    if "--check-2027" in sys.argv:
        check_years(load_config(require_discord=False),
                    scan_limit=max(1, int(_flag_value("--check-2027", "5"))))
        return

    if "--backfill" in sys.argv:
        # No Discord connection needed: this only fetches boards and writes the
        # archive, so it works without a token configured.
        backfill(load_config(require_discord=False),
                 limit=int(_flag_value("--backfill", "0")) or None)
        return

    config = load_config()
    sample = "--sample" in sys.argv
    ping_year = _flag_value("--ping-test", "2027") if "--ping-test" in sys.argv else None
    bot    = SorppleBot(config, sample_mode=sample, ping_test_year=ping_year)
    try:
        bot.run(config["token"], log_handler=None)
    except KeyboardInterrupt:
        log("Sorpple stopped. Bye!")
    finally:
        # Drop the heartbeat so the dashboard reports offline immediately rather
        # than waiting for the staleness window to expire.
        sorpple_control.clear_status()


if __name__ == "__main__":
    main()
