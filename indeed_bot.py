"""
Indeed PH internship monitor -- interactive Discord BOT version.

Same monitoring as indeed_monitor.py (polls ph.indeed.com for the newest
internships, seeds the channel on first run, pings on new listings), but posts
through a gateway-connected bot so each listing carries real buttons:

    [ Apply on company site ]  [ View on Indeed ]  [📋 Job Description]

"Apply on company site" is shown only when the listing has an external ATS URL;
otherwise a single "Apply / View on Indeed" button links directly to the job page.

The "Job Description" button fetches the full description from Indeed's viewjob
page and shows it as an ephemeral message -- visible only to the person who
clicked it.  The button keeps working across bot restarts (discord.py DynamicItem
reconstructs it from the job key baked into the custom_id).

Requires: discord.py >= 2.4   (pip install -U discord.py)

Config (.env): DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, plus the optional
DISCORD_PING / POLL_INTERVAL_SECONDS / INIT_COUNT / FETCH_LIMIT /
INDEED_PROXIES_FILE / INDEED_USE_PROXIES.

Run:  python indeed_bot.py            (monitor continuously)
      python indeed_bot.py --sample   (post one sample listing, then idle so
                                        you can click the buttons to test them)
"""

import asyncio
import os
import sys

import discord
from discord.ext import tasks

from indeed_monitor import (
    EMBED_COLOR,
    SCRIPT_DIR,
    SITE_BASE,
    build_embed,
    fetch_description,
    fetch_jobs,
    html_to_markdown,
    load_dotenv,
    load_proxies,
    load_state,
    log,
    save_state,
)

JD_BUTTON_LABEL = "Job Description"

# Module-level proxy list set once at bot startup; used by the JD button
# callback which cannot easily receive per-request arguments.
_PROXIES: list[str] = []


# --------------------------------------------------------------------------- #
# The "Job Description" button.  DynamicItem matches clicks by the job key
# encoded in the custom_id, so it survives restarts without re-registering a
# view per message.
# --------------------------------------------------------------------------- #
class JobDescriptionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"indeed_jd:(?P<jk>[a-zA-Z0-9]+)",
):
    def __init__(self, jk: str):
        self.jk = jk
        super().__init__(
            discord.ui.Button(
                label=JD_BUTTON_LABEL,
                emoji="📋",
                style=discord.ButtonStyle.secondary,
                custom_id=f"indeed_jd:{jk}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["jk"])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None, fetch_description, self.jk, _PROXIES or None
        )
        text = html_to_markdown(raw) or "No description available — try the listing page."
        embed = discord.Embed(
            title="📋 Job Description",
            description=text[:4096],
            color=EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


def build_view(job: dict) -> discord.ui.View:
    """Build the action row for one Indeed listing.

    Buttons:
      • Apply on company site (link) + View on Indeed (link)
            — when a third-party ATS URL exists and is within Discord's 512-char limit
      • Apply on Indeed (link)
            — when no external URL, or the URL exceeds 512 chars
      • 📋 Job Description (ephemeral) — always present
    """
    job_url   = job["job_url"]
    apply_url = job["apply_url"]
    has_external = apply_url != job_url and len(apply_url) <= 512

    view = discord.ui.View(timeout=None)

    if has_external:
        view.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.link,
                label="Apply on company site",
                url=apply_url,
            )
        )
        view.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.link,
                label="View on Indeed",
                url=job_url,
            )
        )
    else:
        view.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.link,
                label="Apply on Indeed",
                url=job_url,
            )
        )

    view.add_item(JobDescriptionButton(job["id"]))
    return view


# --------------------------------------------------------------------------- #
# Bot.
# --------------------------------------------------------------------------- #
class IndeedMonitorBot(discord.Client):
    def __init__(self, config: dict, sample_mode: bool = False):
        super().__init__(intents=discord.Intents.default())
        self.config      = config
        self.sample_mode = sample_mode

    async def setup_hook(self):
        self.add_dynamic_items(JobDescriptionButton)
        if not self.sample_mode:
            self.poller.change_interval(seconds=self.config["poll_interval"])
            self.poller.start()

    async def on_ready(self):
        log(f"Logged in as {self.user} (id {self.user.id}).")
        if self.sample_mode:
            await self.post_sample()

    async def get_target_channel(self):
        return await self.fetch_channel(int(self.config["channel_id"]))

    async def post_listing(self, channel, job: dict, content: str | None = None):
        embed = discord.Embed.from_dict(
            build_embed(job, include_snippet=False, include_apply_links=False)
        )
        await channel.send(
            content=content,
            embed=embed,
            view=build_view(job),
            allowed_mentions=discord.AllowedMentions(everyone=True, roles=True, users=True),
        )

    async def post_sample(self):
        loop = asyncio.get_running_loop()
        jobs = await loop.run_in_executor(None, fetch_jobs, 8, _PROXIES or None)
        if not jobs:
            log("No jobs returned from Indeed — cannot post sample.")
            return
        sample = jobs[0]
        channel = await self.get_target_channel()
        await self.post_listing(
            channel,
            sample,
            content="**[BOT SAMPLE]** Click 📋 Job Description — it shows only to you. Safe to delete.",
        )
        log(f"Posted sample: {sample['title']}. Click the button to test; Ctrl+C to stop.")

    @tasks.loop(seconds=300)   # interval replaced from config in setup_hook
    async def poller(self):
        try:
            await self.poll_once()
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            log(f"Poll error: {exc!r}; will retry next cycle.")

    @poller.before_loop
    async def before_poller(self):
        await self.wait_until_ready()

    async def poll_once(self):
        loop  = asyncio.get_running_loop()
        jobs  = await loop.run_in_executor(
            None, fetch_jobs, self.config["fetch_limit"], _PROXIES or None
        )
        if not jobs:
            log("No jobs returned from Indeed.")
            return

        channel = await self.get_target_channel()
        state   = load_state()
        seen    = set(state["seen_ids"])

        if not state["initialized"]:
            latest = jobs[: self.config["init_count"]]
            log(f"Initialization: posting top {len(latest)} latest Indeed listings.")
            for job in reversed(latest):
                await self.post_listing(channel, job)
                await asyncio.sleep(1)
            for job in jobs:
                seen.add(job["id"])
            state["initialized"] = True
            state["seen_ids"]    = sorted(seen)
            save_state(state)
            log("Initialization complete.")
            return

        new_jobs = [j for j in jobs if j["id"] not in seen]
        if not new_jobs:
            log("No new Indeed listings.")
            return

        log(f"Found {len(new_jobs)} new Indeed listing(s); posting.")
        ping = self.config["ping"] or None
        for job in reversed(new_jobs):
            content = f"🆕 **New internship** at {job['company']}: {job['title']}"
            if ping:
                content = f"{ping} {content}"
            await self.post_listing(channel, job, content=content)
            seen.add(job["id"])
            await asyncio.sleep(1)

        state["seen_ids"] = sorted(seen)
        save_state(state)


# --------------------------------------------------------------------------- #
# Config + entry point.
# --------------------------------------------------------------------------- #
def load_bot_config() -> dict:
    load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

    token      = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if not token or not channel_id:
        log("ERROR: bot mode needs DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID in .env.")
        sys.exit(1)

    def _int(name, default):
        try:
            return max(1, int(os.environ.get(name, default)))
        except ValueError:
            return default

    proxies_file = os.environ.get(
        "INDEED_PROXIES_FILE",
        os.path.join(SCRIPT_DIR, "proxies.txt"),
    )
    use_proxies = os.environ.get("INDEED_USE_PROXIES", "").strip().lower()
    if use_proxies in ("0", "false", "no", "off"):
        proxies = []
        log("Proxies disabled via INDEED_USE_PROXIES.")
    else:
        proxies = load_proxies(proxies_file)
        if proxies:
            log(f"Loaded {len(proxies)} proxies from {os.path.basename(proxies_file)}.")
        else:
            log("No proxy file found; making direct requests (Cloudflare may block).")

    return {
        "token":          token,
        "channel_id":     channel_id,
        "ping":           os.environ.get("DISCORD_PING", "").strip(),
        "poll_interval":  _int("POLL_INTERVAL_SECONDS", 300),
        "init_count":     _int("INIT_COUNT", 10),
        "fetch_limit":    _int("FETCH_LIMIT", 30),
        "proxies":        proxies,
    }


def main():
    global _PROXIES
    config   = load_bot_config()
    _PROXIES = config.pop("proxies")
    sample   = "--sample" in sys.argv

    bot = IndeedMonitorBot(config, sample_mode=sample)
    try:
        bot.run(config["token"], log_handler=None)
    except KeyboardInterrupt:
        log("Stopped by user. Bye!")


if __name__ == "__main__":
    main()
