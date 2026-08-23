"""
Slash commands for Sorpple — runtime control of the three job-board pollers.

Registered from SorppleBot.setup_hook() via register(bot).  Every command works
purely through the bot's source registry (bot.sources), so adding a fourth job
board needs no change here.

    /status                     what each source is doing right now
    /interval <source> <min>    change a poll interval live (persisted)
    /pause <source>             stop a poller
    /resume <source>            start it again
    /poll <source>              run one cycle immediately

All five require the Manage Server permission and reply ephemerally.
"""

from datetime import datetime, timezone

import discord
from discord import app_commands

from prosple_monitor import log

SOURCE_CHOICES = [
    app_commands.Choice(name="All sources", value="all"),
    app_commands.Choice(name="Prosple", value="prosple"),
    app_commands.Choice(name="JobStreet", value="jobstreet"),
    app_commands.Choice(name="Indeed", value="indeed"),
]

# Below this, Indeed's proxy spend climbs fast enough to be worth a warning.
INDEED_CHEAP_MINUTES = 15

STATUS_COLOR = 0x5865F2   # Discord blurple


def _selected(bot, value: str):
    """Resolve a `source` choice into the list of Source objects it names."""
    if value == "all":
        return list(bot.sources.values())
    return [bot.sources[value]]


def _stamp(moment: datetime | None, style: str = "R") -> str:
    """Render a datetime as a Discord relative timestamp, or an em dash."""
    if moment is None:
        return "—"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f"<t:{int(moment.timestamp())}:{style}>"


def _seen_count(src) -> int:
    try:
        return len(src.load_state().get("seen_ids", []))
    except Exception:  # noqa: BLE001 — /status must never fail on a bad state file
        return 0


def register(bot) -> None:
    tree = bot.tree

    # ── /status ───────────────────────────────────────────────────────────────

    @tree.command(name="status", description="Show what each job-board poller is doing.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def status(interaction: discord.Interaction):
        embed = discord.Embed(
            title="📊 Sorpple status",
            color=STATUS_COLOR,
            timestamp=datetime.now(timezone.utc),
        )

        for src in bot.sources.values():
            running = src.loop is not None and src.loop.is_running()
            head    = "▶ running" if running else "⏸ paused"
            next_at = _stamp(src.loop.next_iteration if running else None)

            lines = [
                f"**{head}** · every **{src.minutes} min** · next {next_at}",
                f"Last poll {_stamp(src.last_poll)} · "
                f"{src.last_new} new last cycle · {_seen_count(src)} seen",
            ]
            if src.last_error:
                lines.append(f"⚠️ Last error: `{src.last_error[:180]}`")

            embed.add_field(name=src.label, value="\n".join(lines), inline=False)

        years = bot.config.get("year_roles") or {}
        embed.add_field(
            name="🎓 Year pings",
            value=(
                ", ".join(f"{y} → {role}" for y, role in sorted(years.items()))
                if years else "none configured — set `PING_ROLE_2027` in .env"
            ),
            inline=False,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # ── /interval ─────────────────────────────────────────────────────────────

    @tree.command(name="interval", description="Change how often a source is polled.")
    @app_commands.describe(
        source="Which job board to change.",
        minutes="Minutes between polls (1–1440).",
    )
    @app_commands.choices(source=SOURCE_CHOICES)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def interval(
        interaction: discord.Interaction,
        source: app_commands.Choice[str],
        # Discord needs literal bounds here; they mirror MIN_MINUTES / MAX_MINUTES
        # in sorpple.py, which clamps the same range when loading .env.
        minutes: app_commands.Range[int, 1, 1440],
    ):
        lines = []
        for src in _selected(bot, source.value):
            src.minutes = int(minutes)
            # change_interval() recalculates the sleep already in flight, so the
            # new value applies to the pending cycle — no restart, no extra poll.
            src.loop.change_interval(minutes=src.minutes)

            if src.loop.is_running():
                lines.append(
                    f"**{src.label}** → every **{src.minutes} min** "
                    f"(next {_stamp(src.loop.next_iteration)})"
                )
            else:
                lines.append(
                    f"**{src.label}** → every **{src.minutes} min** "
                    f"(currently paused — `/resume` to start)"
                )

            if src.key == "indeed" and src.minutes < INDEED_CHEAP_MINUTES:
                lines.append(
                    f"⚠️ Indeed goes through paid proxies — polling every "
                    f"{src.minutes} min will burn through them quickly."
                )
            log(f"[{src.label}] Interval set to {src.minutes} min by {interaction.user}.")

        bot.persist_settings()
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ── /pause ────────────────────────────────────────────────────────────────

    @tree.command(name="pause", description="Stop polling a source.")
    @app_commands.describe(source="Which job board to pause.")
    @app_commands.choices(source=SOURCE_CHOICES)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def pause(interaction: discord.Interaction, source: app_commands.Choice[str]):
        lines = []
        for src in _selected(bot, source.value):
            if not src.loop.is_running():
                lines.append(f"**{src.label}** was already paused.")
                src.paused = True
                continue
            src.loop.cancel()
            src.paused = True
            lines.append(f"⏸ **{src.label}** paused.")
            log(f"[{src.label}] Paused by {interaction.user}.")

        bot.persist_settings()
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ── /resume ───────────────────────────────────────────────────────────────

    @tree.command(name="resume", description="Start polling a source again.")
    @app_commands.describe(source="Which job board to resume.")
    @app_commands.choices(source=SOURCE_CHOICES)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def resume(interaction: discord.Interaction, source: app_commands.Choice[str]):
        lines = []
        for src in _selected(bot, source.value):
            if src.loop.is_running():
                lines.append(f"**{src.label}** was already running.")
                src.paused = False
                continue
            src.loop.change_interval(minutes=src.minutes)
            src.loop.start()
            src.paused = False
            # discord.py runs the loop body immediately on start(), so the first
            # cycle happens now rather than one interval from now.
            lines.append(
                f"▶ **{src.label}** resumed at every **{src.minutes} min** "
                f"— polling once now."
            )
            log(f"[{src.label}] Resumed by {interaction.user}.")

        bot.persist_settings()
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ── /poll ─────────────────────────────────────────────────────────────────

    @tree.command(name="poll", description="Run one poll cycle right now.")
    @app_commands.describe(source="Which job board to poll immediately.")
    @app_commands.choices(source=SOURCE_CHOICES)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def poll(interaction: discord.Interaction, source: app_commands.Choice[str]):
        # A cycle can take a while (fetch + a description fetch per new listing),
        # so acknowledge first and follow up with the result.
        await interaction.response.defer(ephemeral=True, thinking=True)

        lines = []
        for src in _selected(bot, source.value):
            log(f"[{src.label}] Manual poll requested by {interaction.user}.")
            _, summary = await bot.run_source(src.key)
            lines.append(f"**{src.label}**: {summary}")

        lines.append("*The scheduled timer was not reset.*")
        await interaction.followup.send("\n".join(lines), ephemeral=True)
