"""Discord reminder bot: slash commands to add/list/edit/remove recurring
events (birthdays, MOT renewals, permit expiries, ...) and a daily job that
posts reminders to a Discord channel and/or emails them ahead of time.
"""
import logging
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord import app_commands
from discord.ext import commands

from . import config, db, notify
from .dates import RECURRENCES, next_occurrence

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("reminder-bot")

TZ = ZoneInfo(config.TIMEZONE)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def today() -> date:
    return datetime.now(TZ).date()


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"'{value}' isn't a valid date — use YYYY-MM-DD, e.g. 2026-03-14")


def parse_advance_days(value: Optional[str]) -> str:
    raw = value if value is not None else config.DEFAULT_ADVANCE_DAYS
    try:
        days = config.parse_days(raw)
    except ValueError:
        raise ValueError(f"'{raw}' isn't a valid comma-separated list of days, e.g. 7,1,0")
    if not days:
        raise ValueError("advance_days can't be empty — e.g. 7,1,0")
    return ",".join(str(d) for d in sorted(set(days), reverse=True))


def format_event_line(ev: db.Event, occurrence: date, channel_label: Optional[str] = None) -> str:
    days_until = (occurrence - today()).days
    when = "**today**" if days_until == 0 else f"in **{days_until}** day{'s' if days_until != 1 else ''}"
    icon = {"yearly": "🎉", "monthly": "🔁", "once": "📌"}.get(ev.recurrence, "📅")
    line = f"{icon} **{ev.name}** — {when} ({occurrence.isoformat()})"
    if ev.notes:
        line += f"\n    ↳ {ev.notes}"
    if channel_label:
        line += f"\n    📍 {channel_label}"
    return line


def resolve_channel_id(ev: db.Event, settings: db.GuildSettings) -> Optional[str]:
    """An event's own channel overrides the guild's default reminder channel."""
    return ev.channel_id or settings.reminder_channel_id


def channel_label_for(bot_: commands.Bot, channel_id: Optional[str]) -> str:
    if not channel_id:
        return "⚠️ no channel configured"
    channel = bot_.get_channel(int(channel_id))
    return channel.mention if channel else "⚠️ configured channel not found"


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------

async def event_autocomplete(interaction: discord.Interaction, current: str):
    events = await db.get_events(interaction.guild_id)
    current = current.lower()
    matches = [e for e in events if current in e.name.lower()][:25]
    return [
        app_commands.Choice(name=f"{e.name} ({e.event_date.isoformat()})", value=str(e.id))
        for e in matches
    ]


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

event_group = app_commands.Group(
    name="event", description="Manage reminders for birthdays, renewals, and other recurring events"
)


@event_group.command(name="add", description="Add a new event reminder")
@app_commands.describe(
    name="What to call it, e.g. 'Mum's birthday' or 'Car MOT'",
    when="Date in YYYY-MM-DD format, e.g. 2026-03-14",
    recurrence="How it repeats",
    advance_days="Comma-separated days-before to notify, e.g. 7,1,0 (default: 7,1,0)",
    notes="Optional extra detail",
    channel="Which channel this event's reminders post to (default: the server's default reminder channel)",
)
@app_commands.choices(
    recurrence=[app_commands.Choice(name=r, value=r) for r in RECURRENCES]
)
async def event_add(
    interaction: discord.Interaction,
    name: str,
    when: str,
    recurrence: app_commands.Choice[str],
    advance_days: Optional[str] = None,
    notes: Optional[str] = None,
    channel: Optional[discord.TextChannel] = None,
):
    try:
        event_date = parse_date(when)
        advance_csv = parse_advance_days(advance_days)
    except ValueError as e:
        await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
        return

    event_id = await db.add_event(
        guild_id=interaction.guild_id,
        name=name,
        event_date=event_date,
        recurrence=recurrence.value,
        advance_days=advance_csv,
        notes=notes,
        added_by=interaction.user.id,
        channel_id=channel.id if channel else None,
    )
    nxt = next_occurrence(event_date, recurrence.value, today())
    channel_note = f"in {channel.mention}" if channel else "in the server's default reminder channel"
    await interaction.response.send_message(
        f"✅ Added **{name}** (#{event_id}), {recurrence.value}, "
        f"next on {nxt.isoformat() if nxt else 'n/a'}, posting {channel_note}. "
        f"Reminders {advance_csv.replace(',', ', ')} day(s) before.",
        ephemeral=True,
    )


@event_group.command(name="list", description="List upcoming events")
@app_commands.describe(days="Only show events happening within this many days (default 120)")
async def event_list(interaction: discord.Interaction, days: Optional[int] = 120):
    events = await db.get_events(interaction.guild_id)
    settings = await db.get_guild_settings(interaction.guild_id)
    upcoming = []
    for ev in events:
        nxt = next_occurrence(ev.event_date, ev.recurrence, today())
        if nxt is None:
            continue
        if (nxt - today()).days <= days:
            upcoming.append((nxt, ev))
    upcoming.sort(key=lambda pair: pair[0])

    if not upcoming:
        await interaction.response.send_message(
            f"No events in the next {days} days.", ephemeral=True
        )
        return

    lines = [
        format_event_line(ev, nxt, channel_label_for(bot, resolve_channel_id(ev, settings)))
        for nxt, ev in upcoming
    ]
    embed = discord.Embed(
        title=f"📋 Upcoming events (next {days} days)",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@event_group.command(name="remove", description="Remove an event")
@app_commands.describe(event="The event to remove")
@app_commands.autocomplete(event=event_autocomplete)
async def event_remove(interaction: discord.Interaction, event: str):
    ev = await db.get_event(int(event), interaction.guild_id)
    if not ev:
        await interaction.response.send_message("⚠️ Couldn't find that event.", ephemeral=True)
        return
    await db.remove_event(ev.id, interaction.guild_id)
    await interaction.response.send_message(f"🗑️ Removed **{ev.name}**.", ephemeral=True)


@event_group.command(name="edit", description="Edit an existing event")
@app_commands.describe(
    event="The event to edit",
    name="New name (optional)",
    when="New date, YYYY-MM-DD (optional)",
    recurrence="New recurrence (optional)",
    advance_days="New comma-separated days-before, e.g. 7,1,0 (optional)",
    notes="New notes (optional)",
    channel="New channel for this event's reminders (optional)",
    clear_channel="Reset to the server's default reminder channel (optional)",
)
@app_commands.choices(
    recurrence=[app_commands.Choice(name=r, value=r) for r in RECURRENCES]
)
@app_commands.autocomplete(event=event_autocomplete)
async def event_edit(
    interaction: discord.Interaction,
    event: str,
    name: Optional[str] = None,
    when: Optional[str] = None,
    recurrence: Optional[app_commands.Choice[str]] = None,
    advance_days: Optional[str] = None,
    notes: Optional[str] = None,
    channel: Optional[discord.TextChannel] = None,
    clear_channel: Optional[bool] = False,
):
    ev = await db.get_event(int(event), interaction.guild_id)
    if not ev:
        await interaction.response.send_message("⚠️ Couldn't find that event.", ephemeral=True)
        return

    if channel is not None and clear_channel:
        await interaction.response.send_message(
            "⚠️ Pick either `channel` or `clear_channel`, not both.", ephemeral=True
        )
        return

    fields = {}
    try:
        if name is not None:
            fields["name"] = name
        if when is not None:
            fields["event_date"] = parse_date(when)
        if recurrence is not None:
            fields["recurrence"] = recurrence.value
        if advance_days is not None:
            fields["advance_days"] = parse_advance_days(advance_days)
        if notes is not None:
            fields["notes"] = notes
        if channel is not None:
            fields["channel_id"] = str(channel.id)
        elif clear_channel:
            fields["channel_id"] = None
    except ValueError as e:
        await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
        return

    if not fields:
        await interaction.response.send_message("Nothing to change.", ephemeral=True)
        return

    await db.update_event(ev.id, interaction.guild_id, **fields)
    await interaction.response.send_message(f"✏️ Updated **{ev.name}**.", ephemeral=True)


@event_group.command(name="setchannel", description="Set which channel reminders get posted to")
@app_commands.describe(channel="The channel to post reminders in")
@app_commands.checks.has_permissions(manage_guild=True)
async def event_setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    await db.set_guild_channel(interaction.guild_id, channel.id)
    await interaction.response.send_message(f"📌 Reminders will be posted in {channel.mention}.", ephemeral=True)


@event_group.command(name="setemails", description="Set email addresses to also notify (comma-separated)")
@app_commands.describe(emails="Comma-separated email addresses, e.g. a@x.com,b@y.com")
@app_commands.checks.has_permissions(manage_guild=True)
async def event_setemails(interaction: discord.Interaction, emails: str):
    if not config.EMAIL_ENABLED:
        await interaction.response.send_message(
            "⚠️ Email isn't configured on this bot instance (no SMTP settings set), "
            "so addresses will be saved but nothing will be emailed yet.",
            ephemeral=True,
        )
    addr_list = [e.strip() for e in emails.split(",") if e.strip()]
    await db.set_guild_emails(interaction.guild_id, addr_list)
    if config.EMAIL_ENABLED:
        await interaction.response.send_message(
            f"📧 Will email: {', '.join(addr_list) if addr_list else '(none)'}", ephemeral=True
        )


@event_group.command(name="test", description="Send a test reminder for an event right now")
@app_commands.describe(event="The event to test")
@app_commands.autocomplete(event=event_autocomplete)
async def event_test(interaction: discord.Interaction, event: str):
    ev = await db.get_event(int(event), interaction.guild_id)
    if not ev:
        await interaction.response.send_message("⚠️ Couldn't find that event.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    nxt = next_occurrence(ev.event_date, ev.recurrence, today())
    line = format_event_line(ev, nxt) if nxt else f"**{ev.name}** has no upcoming occurrence."
    settings = await db.get_guild_settings(interaction.guild_id)
    chan_id = resolve_channel_id(ev, settings)

    results = []
    if chan_id:
        channel = bot.get_channel(int(chan_id))
        if channel:
            await channel.send(embed=discord.Embed(
                title="🔔 Test reminder", description=line, color=discord.Color.orange()
            ))
            results.append(f"posted to {channel.mention}")
        else:
            results.append("⚠️ configured channel not found")
    else:
        results.append("no reminder channel configured (set one on the event, or with /event setchannel)")

    if settings.email_recipients and config.EMAIL_ENABLED:
        try:
            await notify.send_email("Test reminder", line.replace("**", ""), settings.email_recipients)
            results.append(f"emailed {', '.join(settings.email_recipients)}")
        except Exception as e:
            results.append(f"⚠️ email failed: {e}")

    await interaction.followup.send("Test sent — " + "; ".join(results), ephemeral=True)


bot.tree.add_command(event_group)


# ---------------------------------------------------------------------------
# Daily reminder check
# ---------------------------------------------------------------------------

async def check_reminders() -> None:
    the_day = today()
    for guild_id_str in await db.all_guild_ids():
        guild_id = int(guild_id_str)
        settings = await db.get_guild_settings(guild_id)
        events = await db.get_events(guild_id)

        # Due events, grouped by resolved channel (event's own, else guild default;
        # None groups events where neither is configured).
        due_by_channel: dict[Optional[str], list[tuple[db.Event, date, int]]] = {}
        for ev in events:
            nxt = next_occurrence(ev.event_date, ev.recurrence, the_day)
            if nxt is None:
                await db.mark_inactive(ev.id)
                continue
            days_until = (nxt - the_day).days
            advance_list = config.parse_days(ev.advance_days)
            if days_until in advance_list:
                if await db.record_sent(ev.id, nxt, days_until):
                    chan_id = resolve_channel_id(ev, settings)
                    due_by_channel.setdefault(chan_id, []).append((ev, nxt, days_until))

        if not due_by_channel:
            continue

        # Discord: one embed per resolved channel, so events split across
        # channels (e.g. shared vs. personal) don't all land in one place.
        for chan_id, due in due_by_channel.items():
            body = "\n".join(format_event_line(ev, nxt) for ev, nxt, _ in due)
            if chan_id:
                channel = bot.get_channel(int(chan_id))
                if channel:
                    embed = discord.Embed(title="🔔 Reminders", description=body, color=discord.Color.gold())
                    await channel.send(embed=embed)
                else:
                    log.warning("Guild %s: channel %s no longer exists", guild_id, chan_id)
            else:
                log.warning(
                    "Guild %s has due reminders with no channel configured "
                    "(set one on the event, or with /event setchannel)", guild_id,
                )

        # Email stays one-for-all: a single combined summary across every channel.
        if settings.email_recipients and config.EMAIL_ENABLED:
            all_due = [pair for due in due_by_channel.values() for pair in due]
            body = "\n".join(format_event_line(ev, nxt) for ev, nxt, _ in all_due)
            try:
                await notify.send_email("Upcoming reminders", body.replace("**", ""), settings.email_recipients)
            except Exception:
                log.exception("Failed to send reminder email for guild %s", guild_id)


scheduler = AsyncIOScheduler(timezone=TZ)


@bot.event
async def on_ready():
    log.info("Logged in as %s", bot.user)
    await db.init()

    if config.DISCORD_TEST_GUILD_ID:
        guild = discord.Object(id=int(config.DISCORD_TEST_GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        log.info("Synced commands to test guild %s", config.DISCORD_TEST_GUILD_ID)
    else:
        await bot.tree.sync()
        log.info("Synced global commands (can take up to ~1hr to appear everywhere)")

    if not scheduler.running:
        scheduler.add_job(
            check_reminders,
            CronTrigger(hour=config.REMINDER_HOUR, minute=config.REMINDER_MINUTE, timezone=TZ),
            id="daily_reminder_check",
            replace_existing=True,
        )
        scheduler.start()
        log.info(
            "Scheduler started: daily check at %02d:%02d %s",
            config.REMINDER_HOUR, config.REMINDER_MINUTE, config.TIMEZONE,
        )


def main():
    if not config.DISCORD_BOT_TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN is not set (check your .env file)")
    bot.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
