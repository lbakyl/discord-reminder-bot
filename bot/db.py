"""SQLite storage layer (async, via aiosqlite)."""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import aiosqlite

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    name TEXT NOT NULL,
    event_date TEXT NOT NULL,          -- ISO date, reference occurrence
    recurrence TEXT NOT NULL,          -- once | yearly | monthly
    advance_days TEXT NOT NULL,        -- csv ints, e.g. "7,1,0"
    notes TEXT,
    added_by TEXT,
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    channel_id TEXT              -- overrides guild_settings.reminder_channel_id when set
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id TEXT PRIMARY KEY,
    reminder_channel_id TEXT,
    email_recipients TEXT              -- csv of email addresses
);

CREATE TABLE IF NOT EXISTS sent_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    occurrence_date TEXT NOT NULL,
    days_before INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    UNIQUE(event_id, occurrence_date, days_before)
);
"""


@dataclass
class Event:
    id: int
    guild_id: str
    name: str
    event_date: date
    recurrence: str
    advance_days: str
    notes: Optional[str]
    added_by: Optional[str]
    active: bool
    channel_id: Optional[str]

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "Event":
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            name=row["name"],
            event_date=date.fromisoformat(row["event_date"]),
            recurrence=row["recurrence"],
            advance_days=row["advance_days"],
            notes=row["notes"],
            added_by=row["added_by"],
            active=bool(row["active"]),
            channel_id=row["channel_id"],
        )


@dataclass
class GuildSettings:
    guild_id: str
    reminder_channel_id: Optional[str]
    email_recipients: list[str]


_db: Optional[aiosqlite.Connection] = None


async def init() -> None:
    global _db
    _db = await aiosqlite.connect(config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA)
    await _migrate()
    await _db.commit()


async def _migrate() -> None:
    """Add columns to pre-existing databases that predate them."""
    cur = await _db.execute("PRAGMA table_info(events)")
    cols = {row["name"] for row in await cur.fetchall()}
    if "channel_id" not in cols:
        await _db.execute("ALTER TABLE events ADD COLUMN channel_id TEXT")


def _conn() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("db.init() must be called before using the database")
    return _db


async def close() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def add_event(
    guild_id: int,
    name: str,
    event_date: date,
    recurrence: str,
    advance_days: str,
    notes: Optional[str],
    added_by: int,
    channel_id: Optional[int] = None,
) -> int:
    cur = await _conn().execute(
        """INSERT INTO events
           (guild_id, name, event_date, recurrence, advance_days, notes, added_by, created_at, active, channel_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (
            str(guild_id),
            name,
            event_date.isoformat(),
            recurrence,
            advance_days,
            notes,
            str(added_by),
            datetime.utcnow().isoformat(),
            str(channel_id) if channel_id is not None else None,
        ),
    )
    await _conn().commit()
    return cur.lastrowid


async def get_events(guild_id: int, active_only: bool = True) -> list[Event]:
    query = "SELECT * FROM events WHERE guild_id = ?"
    params: tuple = (str(guild_id),)
    if active_only:
        query += " AND active = 1"
    query += " ORDER BY name COLLATE NOCASE"
    cur = await _conn().execute(query, params)
    rows = await cur.fetchall()
    return [Event.from_row(r) for r in rows]


async def get_event(event_id: int, guild_id: int) -> Optional[Event]:
    cur = await _conn().execute(
        "SELECT * FROM events WHERE id = ? AND guild_id = ?",
        (event_id, str(guild_id)),
    )
    row = await cur.fetchone()
    return Event.from_row(row) if row else None


async def remove_event(event_id: int, guild_id: int) -> bool:
    cur = await _conn().execute(
        "DELETE FROM events WHERE id = ? AND guild_id = ?",
        (event_id, str(guild_id)),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def update_event(event_id: int, guild_id: int, **fields) -> bool:
    if not fields:
        return False
    if "event_date" in fields and isinstance(fields["event_date"], date):
        fields["event_date"] = fields["event_date"].isoformat()
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [event_id, str(guild_id)]
    cur = await _conn().execute(
        f"UPDATE events SET {cols} WHERE id = ? AND guild_id = ?", values
    )
    await _conn().commit()
    return cur.rowcount > 0


async def mark_inactive(event_id: int) -> None:
    await _conn().execute("UPDATE events SET active = 0 WHERE id = ?", (event_id,))
    await _conn().commit()


async def all_guild_ids() -> list[str]:
    cur = await _conn().execute("SELECT DISTINCT guild_id FROM events")
    rows = await cur.fetchall()
    return [r["guild_id"] for r in rows]


async def get_guild_settings(guild_id: int) -> GuildSettings:
    cur = await _conn().execute(
        "SELECT * FROM guild_settings WHERE guild_id = ?", (str(guild_id),)
    )
    row = await cur.fetchone()
    if not row:
        return GuildSettings(guild_id=str(guild_id), reminder_channel_id=None, email_recipients=[])
    emails = [e.strip() for e in (row["email_recipients"] or "").split(",") if e.strip()]
    return GuildSettings(
        guild_id=row["guild_id"],
        reminder_channel_id=row["reminder_channel_id"],
        email_recipients=emails,
    )


async def set_guild_channel(guild_id: int, channel_id: int) -> None:
    await _conn().execute(
        """INSERT INTO guild_settings (guild_id, reminder_channel_id)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET reminder_channel_id = excluded.reminder_channel_id""",
        (str(guild_id), str(channel_id)),
    )
    await _conn().commit()


async def set_guild_emails(guild_id: int, emails: list[str]) -> None:
    csv = ",".join(e.strip() for e in emails if e.strip())
    await _conn().execute(
        """INSERT INTO guild_settings (guild_id, email_recipients)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET email_recipients = excluded.email_recipients""",
        (str(guild_id), csv),
    )
    await _conn().commit()


async def record_sent(event_id: int, occurrence_date: date, days_before: int) -> bool:
    """Returns True if this was newly recorded (i.e. not sent before)."""
    try:
        await _conn().execute(
            """INSERT INTO sent_reminders (event_id, occurrence_date, days_before, sent_at)
               VALUES (?, ?, ?, ?)""",
            (event_id, occurrence_date.isoformat(), days_before, datetime.utcnow().isoformat()),
        )
        await _conn().commit()
        return True
    except aiosqlite.IntegrityError:
        return False
