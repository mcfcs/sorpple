"""
Sorpple — unified Discord bot for Prosple PH, Indeed PH, and JobStreet PH.

Runs all three internship monitors as a single Discord gateway connection with
three independent polling tasks and all button handlers registered in one client.
This is the recommended way to run Sorpple.

Run:  python sorpple.py
      python sorpple.py --sample   (post one listing from each source then idle)
"""

import asyncio
import os
import sys

import discord
from discord.ext import tasks

# ── Monitor modules (data + embed layer) ──────────────────────────────────────
from prosple_monitor import (
    SCRIPT_DIR,
    _safe,
    build_embed as prosple_build_embed,
    fetch_internships,
    load_dotenv,
    load_state as prosple_load_state,
    log,
    save_state as prosple_save_state,
)
from indeed_monitor import load_proxies
from indeed_monitor import (
    build_embed as indeed_build_embed,
    fetch_jobs as indeed_fetch_jobs,
    load_state as indeed_load_state,
    save_state as indeed_save_state,
)
from jobstreet_monitor import (
    build_embed as js_build_embed,
    fetch_jobs as js_fetch_jobs,
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


# ─────────────────────────────────────────────────────────────────────────────

class SorppleBot(discord.Client):
    def __init__(self, config: dict, sample_mode: bool = False):
        super().__init__(intents=discord.Intents.default())
        self.config      = config
        self.sample_mode = sample_mode

    async def setup_hook(self):
        # Register all three button namespaces so old messages stay clickable.
        self.add_dynamic_items(ProspleJDButton)
        self.add_dynamic_items(IndeedJDButton)
        self.add_dynamic_items(JSJDButton)

        if not self.sample_mode:
            ivl = self.config["poll_interval"]
            for poller in (self.prosple_poller, self.indeed_poller, self.js_poller):
                poller.change_interval(seconds=ivl)
                poller.start()

    async def on_ready(self):
        log(f"Sorpple logged in as {self.user} (id {self.user.id}).")
        if self.sample_mode:
            await self._post_samples()

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

        for label, fetch, build, view_fn, kwargs in [
            (
                "Prosple",
                lambda: fetch_internships(5),
                lambda o: prosple_build_embed(o, include_full_description=False, include_apply_links=False),
                prosple_build_view,
                {},
            ),
            (
                "Indeed",
                lambda: indeed_fetch_jobs(5, _ib._PROXIES or None),
                lambda j: indeed_build_embed(j, include_snippet=False, include_apply_links=False),
                indeed_build_view,
                {},
            ),
            (
                "JobStreet",
                lambda: js_fetch_jobs(5, _jb._PROXIES or None),
                lambda j: js_build_embed(j, include_teaser=False, include_apply_links=False),
                js_build_view,
                {},
            ),
        ]:
            try:
                items = await loop.run_in_executor(None, fetch)
                if items:
                    item = items[0]
                    await self._send(channel, build(item), view_fn(item),
                                     content=f"**[{label}]** {note}")
                    await asyncio.sleep(1)
            except Exception as exc:
                log(f"[{label}] Sample error: {exc!r}")

        log("Samples posted. Click the buttons to test; Ctrl+C to stop.")

    # ── Prosple polling ───────────────────────────────────────────────────────

    @tasks.loop(seconds=300)
    async def prosple_poller(self):
        try:
            await self._poll_prosple()
        except Exception as exc:
            log(f"[Prosple] Poll error: {exc!r}")

    @prosple_poller.before_loop
    async def _before_prosple(self):
        await self.wait_until_ready()

    async def _poll_prosple(self):
        loop  = asyncio.get_running_loop()
        opps  = await loop.run_in_executor(None, fetch_internships, self.config["fetch_limit"])
        if not opps:
            log("[Prosple] No internships returned.")
            return

        channel = await self._channel()
        state   = prosple_load_state()
        seen    = set(state["seen_ids"])

        if not state["initialized"]:
            latest = opps[: self.config["init_count"]]
            log(f"[Prosple] Init: posting {len(latest)} listings.")
            for opp in reversed(latest):
                await self._send(
                    channel,
                    prosple_build_embed(opp, include_full_description=False, include_apply_links=False),
                    prosple_build_view(opp),
                )
                await asyncio.sleep(1)
            for opp in opps:
                seen.add(opp["id"])
            state.update(initialized=True, seen_ids=sorted(seen))
            prosple_save_state(state)
            log("[Prosple] Init complete.")
            return

        new = [o for o in opps if o["id"] not in seen]
        if not new:
            log("[Prosple] No new internships.")
            return

        log(f"[Prosple] {len(new)} new listing(s).")
        ping = self.config.get("ping") or None
        for opp in reversed(new):
            employer = _safe(opp, "parentEmployer", "title") or "an employer"
            content  = f"🆕 **New internship** at {employer}: {opp.get('title', 'internship')}"
            if ping:
                content = f"{ping} {content}"
            await self._send(
                channel,
                prosple_build_embed(opp, include_full_description=False, include_apply_links=False),
                prosple_build_view(opp),
                content=content,
            )
            seen.add(opp["id"])
            await asyncio.sleep(1)

        state["seen_ids"] = sorted(seen)
        prosple_save_state(state)

    # ── Indeed polling ────────────────────────────────────────────────────────

    @tasks.loop(seconds=300)
    async def indeed_poller(self):
        try:
            await self._poll_indeed()
        except Exception as exc:
            log(f"[Indeed] Poll error: {exc!r}")

    @indeed_poller.before_loop
    async def _before_indeed(self):
        await self.wait_until_ready()

    async def _poll_indeed(self):
        loop = asyncio.get_running_loop()
        jobs = await loop.run_in_executor(
            None, indeed_fetch_jobs, self.config["fetch_limit"], _ib._PROXIES or None
        )
        if not jobs:
            log("[Indeed] No jobs returned.")
            return

        channel = await self._channel()
        state   = indeed_load_state()
        seen    = set(state["seen_ids"])

        if not state["initialized"]:
            latest = jobs[: self.config["init_count"]]
            log(f"[Indeed] Init: posting {len(latest)} listings.")
            for job in reversed(latest):
                await self._send(
                    channel,
                    indeed_build_embed(job, include_snippet=False, include_apply_links=False),
                    indeed_build_view(job),
                )
                await asyncio.sleep(1)
            for job in jobs:
                seen.add(job["id"])
            state.update(initialized=True, seen_ids=sorted(seen))
            indeed_save_state(state)
            log("[Indeed] Init complete.")
            return

        new = [j for j in jobs if j["id"] not in seen]
        if not new:
            log("[Indeed] No new listings.")
            return

        log(f"[Indeed] {len(new)} new listing(s).")
        ping = self.config.get("ping") or None
        for job in reversed(new):
            content = f"🆕 **New internship** at {job['company']}: {job['title']}"
            if ping:
                content = f"{ping} {content}"
            await self._send(
                channel,
                indeed_build_embed(job, include_snippet=False, include_apply_links=False),
                indeed_build_view(job),
                content=content,
            )
            seen.add(job["id"])
            await asyncio.sleep(1)

        state["seen_ids"] = sorted(seen)
        indeed_save_state(state)

    # ── JobStreet polling ─────────────────────────────────────────────────────

    @tasks.loop(seconds=300)
    async def js_poller(self):
        try:
            await self._poll_jobstreet()
        except Exception as exc:
            log(f"[JobStreet] Poll error: {exc!r}")

    @js_poller.before_loop
    async def _before_js(self):
        await self.wait_until_ready()

    async def _poll_jobstreet(self):
        loop = asyncio.get_running_loop()
        jobs = await loop.run_in_executor(
            None, js_fetch_jobs, self.config["fetch_limit"], _jb._PROXIES or None
        )
        if not jobs:
            log("[JobStreet] No jobs returned.")
            return

        channel = await self._channel()
        state   = js_load_state()
        seen    = set(state["seen_ids"])

        if not state["initialized"]:
            latest = jobs[: self.config["init_count"]]
            log(f"[JobStreet] Init: posting {len(latest)} listings.")
            for job in reversed(latest):
                await self._send(
                    channel,
                    js_build_embed(job, include_teaser=False, include_apply_links=False),
                    js_build_view(job),
                )
                await asyncio.sleep(1)
            for job in jobs:
                seen.add(job["id"])
            state.update(initialized=True, seen_ids=sorted(seen))
            js_save_state(state)
            log("[JobStreet] Init complete.")
            return

        new = [j for j in jobs if j["id"] not in seen]
        if not new:
            log("[JobStreet] No new listings.")
            return

        log(f"[JobStreet] {len(new)} new listing(s).")
        ping = self.config.get("ping") or None
        for job in reversed(new):
            content = f"🆕 **New internship** at {job['company']}: {job['title']}"
            if ping:
                content = f"{ping} {content}"
            await self._send(
                channel,
                js_build_embed(job, include_teaser=False, include_apply_links=False),
                js_build_view(job),
                content=content,
            )
            seen.add(job["id"])
            await asyncio.sleep(1)

        state["seen_ids"] = sorted(seen)
        js_save_state(state)


# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

    token      = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if not token or not channel_id:
        log("ERROR: DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID must be set in .env.")
        sys.exit(1)

    def _int(name, default):
        try:
            return max(1, int(os.environ.get(name, default)))
        except ValueError:
            return default

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

    return {
        "token":         token,
        "channel_id":    channel_id,
        "ping":          os.environ.get("DISCORD_PING", "").strip(),
        "poll_interval": _int("POLL_INTERVAL_SECONDS", 300),
        "init_count":    _int("INIT_COUNT", 10),
        "fetch_limit":   _int("FETCH_LIMIT", 30),
    }


def main():
    config = load_config()
    sample = "--sample" in sys.argv
    bot    = SorppleBot(config, sample_mode=sample)
    try:
        bot.run(config["token"], log_handler=None)
    except KeyboardInterrupt:
        log("Sorpple stopped. Bye!")


if __name__ == "__main__":
    main()
