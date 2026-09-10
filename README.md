# Discord Reminder Bot

Self-hosted Discord bot for tracking recurring life-admin dates — birthdays,
car MOT, parking permits, anything with a "don't forget this" date — and
getting reminders in Discord (and optionally by email) ahead of time.

All interaction happens as Discord slash commands, no separate web UI:

- `/event add` — add an event (name, date, how it repeats, how many days ahead to warn you)
- `/event list` — see what's coming up
- `/event edit` — change a date, notes, etc.
- `/event remove` — delete one
- `/event setchannel` — pick which channel reminders get posted to
- `/event setemails` — also email a list of addresses when reminders fire
- `/event test` — fire a reminder for one event right now, to check it's wired up correctly

Recurrence supports `once`, `yearly` (birthdays, MOT, permits) and `monthly`.
Yearly handles Feb 29 birthdays sanely (falls back to Feb 28 on non-leap years).
Reminders default to firing 7 days, 1 day, and on-the-day before an event —
configurable per event via `advance_days`, e.g. `30,7,1,0`.

## 1. Create the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. **Bot** tab → **Reset Token** → copy it (this is `DISCORD_BOT_TOKEN`). Keep it secret.
3. No privileged intents are needed (the bot never reads message content).
4. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`; bot permissions `Send Messages` + `Embed Links`.
   Or just use this (replace `YOUR_CLIENT_ID` with the Application ID from the **General Information** tab):
   ```
   https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=18432&scope=bot%20applications.commands
   ```
5. Open that URL, invite the bot to your server.

## 2. Configure

```bash
cp .env.example .env
```

Fill in `.env`:
- `DISCORD_BOT_TOKEN` — from step 1.
- `TIMEZONE` — e.g. `Europe/London`.
- `DEFAULT_ADVANCE_DAYS` — default lead time(s) for new events, e.g. `7,1,0`.
- Email (optional) — `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`.
  For Gmail: host `smtp.gmail.com`, port `465`, and an **App Password**
  (not your normal password) from <https://myaccount.google.com/apppasswords>.
  **Put the password directly in your local `.env` file — never share it in chat, a
  ticket, or anywhere else; `.env` is already git-ignored.**

## 3. Run

```bash
docker compose up -d --build
```

Data persists in `./data/reminders.db` on the host (mounted as a volume), so
rebuilding/updating the container doesn't lose your events.

Then in Discord:
```
/event setchannel #reminders
/event setemails you@example.com, partner@example.com
/event add name:"Mum's Birthday" when:1970-03-14 recurrence:yearly
/event add name:"Car MOT" when:2026-11-02 recurrence:yearly advance_days:30,14,1
/event test event:"Mum's Birthday"
```

Global slash commands can take up to ~1 hour to show up everywhere the first
time. To iterate faster during setup, set `DISCORD_TEST_GUILD_ID` in `.env`
to your server's ID — commands then register there instantly on restart.

## Notes

- Storage is a single SQLite file — fine for household-scale use, no external DB needed.
- The daily check runs once at `REMINDER_HOUR:REMINDER_MINUTE` in `TIMEZONE`. It's idempotent
  (won't double-send even if the bot restarts the same day).
- Anyone in the server can add/list/remove events; `setchannel` and `setemails` require
  the "Manage Server" permission, to stop random members reconfiguring where reminders go.
