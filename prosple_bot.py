"""
Prosple PH internship monitor -- interactive Discord BOT version.

Same monitoring as prosple_monitor.py (polls ph.prosple.com for the newest
internships, seeds the channel on first run, pings on new listings), but posts
through a gateway-connected bot so each listing carries real buttons:

    [ Apply ]  [ View on Prosple ]  [📋 Job Description]

The "Job Description" button opens the full description as an **ephemeral**
message -- visible only to the person who clicked it. No spoiler blur, no
channel clutter. The button keeps working across bot restarts (discord.py
DynamicItem reconstructs it from the listing id baked into the custom_id).

Because it must be there to answer clicks, this bot has to run continuously
(it cannot be scheduled as a one-shot like `--once`).

Requires: discord.py >= 2.4   (pip install -U discord.py)
Reuses all the data/formatting logic from prosple_monitor.py.

Config (.env): DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, plus the optional
DISCORD_PING / POLL_INTERVAL_SECONDS / INIT_COUNT / FETCH_LIMIT.

Run:  python prosple_bot.py            (monitor continuously)
      python prosple_bot.py --sample   (post one sample listing, then idle so
                                         you can click the button to test it)
"""

import asyncio
import os
import sys

import discord
from discord.ext import tasks

from prosple_monitor import (
    EMBED_COLOR,
    SCRIPT_DIR,
    SITE_BASE,
    _safe,
    build_embed,
    fetch_description,
    fetch_internships,
    html_to_markdown,
    load_dotenv,
    load_state,
    log,
    resolve_apply,
    save_state,
)

JD_BUTTON_LABEL = "Job Description"


def detail_url_of(opp):
    path = opp.get("detailPageURL") or ""
    return SITE_BASE + path if path.startswith("/") else path


# --------------------------------------------------------------------------- #
# The "Job Description" button. DynamicItem matches clicks by the listing id
# encoded in the custom_id, so it survives restarts without re-registering a
# view per message.
# --------------------------------------------------------------------------- #
class JobDescriptionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"jd:(?P<opp_id>\d+)",
):
    def __init__(self, opp_id):
        self.opp_id = str(opp_id)
        super().__init__(
            discord.ui.Button(
                label=JD_BUTTON_LABEL,
                emoji="📋",
                style=discord.ButtonStyle.secondary,
                custom_id=f"jd:{self.opp_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["opp_id"])

    async def callback(self, interaction):
        # Acknowledge privately within 3s, then fetch + reply only to clicker.
        await interaction.response.defer(ephemeral=True, thinking=True)
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, fetch_description, self.opp_id)
        text = html_to_markdown(raw) or "No description available — try the listing page."
        embed = discord.Embed(
            title="📋 See Job description",
            description=text[:4096],
            color=EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


def build_view(opp):
    """An action row: Apply (link) + View on Prosple (link) + JD button."""
    detail_url = detail_url_of(opp)
    apply_label, apply_url = resolve_apply(opp, detail_url)
    view = discord.ui.View(timeout=None)
    if apply_url:
        view.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.link,
                label=apply_label.replace("↗", "").strip()[:80],
                url=apply_url,
            )
        )
    if apply_url != detail_url and detail_url:
        view.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.link, label="View on Prosple", url=detail_url
            )
        )
    view.add_item(JobDescriptionButton(opp["id"]))
    return view


# --------------------------------------------------------------------------- #
# Bot.
# --------------------------------------------------------------------------- #
class MonitorBot(discord.Client):
    def __init__(self, config, sample_mode=False):
        super().__init__(intents=discord.Intents.default())
        self.config = config
        self.sample_mode = sample_mode

    async def setup_hook(self):
        # Make every existing "Job Description" button clickable after a restart.
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

    async def post_listing(self, channel, opp, content=None):
        # build_embed() returns a plain dict (for the raw webhook API); discord.py
        # needs a discord.Embed, so convert it.
        embed = discord.Embed.from_dict(
            build_embed(opp, include_full_description=False, include_apply_links=False)
        )
        await channel.send(
            content=content,
            embed=embed,
            view=build_view(opp),
            allowed_mentions=discord.AllowedMentions(everyone=True, roles=True, users=True),
        )

    async def post_sample(self):
        loop = asyncio.get_running_loop()
        opps = await loop.run_in_executor(None, fetch_internships, 8)
        sample = next((o for o in opps if (o.get("applyByUrl") or o.get("url"))), opps[0])
        channel = await self.get_target_channel()
        await self.post_listing(
            channel,
            sample,
            content="**[BOT SAMPLE]** Click 📋 Job Description — it shows only to you. Safe to delete.",
        )
        log(f"Posted sample: {sample['title']}. Click the button to test; Ctrl+C to stop.")

    @tasks.loop(seconds=300)  # interval replaced from config in setup_hook
    async def poller(self):
        try:
            await self.poll_once()
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            log(f"Poll error: {exc!r}; will retry next cycle.")

    @poller.before_loop
    async def before_poller(self):
        await self.wait_until_ready()

    async def poll_once(self):
        loop = asyncio.get_running_loop()
        opportunities = await loop.run_in_executor(
            None, fetch_internships, self.config["fetch_limit"]
        )
        if not opportunities:
            log("No internships returned by the API.")
            return

        channel = await self.get_target_channel()
        state = load_state()
        seen = set(state["seen_ids"])

        if not state["initialized"]:
            latest = opportunities[: self.config["init_count"]]
            log(f"Initialization: posting top {len(latest)} latest internships.")
            for opp in reversed(latest):  # oldest-first so newest lands last
                await self.post_listing(channel, opp)
                await asyncio.sleep(1)
            for opp in opportunities:  # whole current board becomes the baseline
                seen.add(opp["id"])
            state["initialized"] = True
            state["seen_ids"] = sorted(seen)
            save_state(state)
            log("Initialization complete.")
            return

        new_opps = [opp for opp in opportunities if opp["id"] not in seen]
        if not new_opps:
            log("No new internships.")
            return

        log(f"Found {len(new_opps)} new internship(s); posting.")
        ping = self.config["ping"] or None
        for opp in reversed(new_opps):
            employer = _safe(opp, "parentEmployer", "title") or "an employer"
            content = f"🆕 **New internship** at {employer}: {opp.get('title', 'internship')}"
            if ping:
                content = f"{ping} {content}"
            await self.post_listing(channel, opp, content=content)
            seen.add(opp["id"])
            await asyncio.sleep(1)
        state["seen_ids"] = sorted(seen)
        save_state(state)


def load_bot_config():
    load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if not token or not channel_id:
        log("ERROR: bot mode needs DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID in .env.")
        sys.exit(1)

    def _int(name, default):
        try:
            return max(1, int(os.environ.get(name, default)))
        except ValueError:
            return default

    return {
        "token": token,
        "channel_id": channel_id,
        "ping": os.environ.get("DISCORD_PING", "").strip(),
        "poll_interval": _int("POLL_INTERVAL_SECONDS", 300),
        "init_count": _int("INIT_COUNT", 10),
        "fetch_limit": _int("FETCH_LIMIT", 30),
    }


def main():
    config = load_bot_config()
    sample = "--sample" in sys.argv
    bot = MonitorBot(config, sample_mode=sample)
    try:
        bot.run(config["token"], log_handler=None)
    except KeyboardInterrupt:
        log("Stopped by user. Bye!")


if __name__ == "__main__":
    main()
