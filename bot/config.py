"""Loads configuration from environment variables (populated from .env)."""
import os
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val not in (None, "") else default


DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_TEST_GUILD_ID = os.getenv("DISCORD_TEST_GUILD_ID") or None

TIMEZONE = os.getenv("TIMEZONE", "Europe/London")
REMINDER_HOUR = _int("REMINDER_HOUR", 8)
REMINDER_MINUTE = _int("REMINDER_MINUTE", 0)
DEFAULT_ADVANCE_DAYS = os.getenv("DEFAULT_ADVANCE_DAYS", "7,1,0")

DB_PATH = os.getenv("DB_PATH", "data/reminders.db")

SMTP_HOST = os.getenv("SMTP_HOST") or None
SMTP_PORT = _int("SMTP_PORT", 465)
SMTP_USER = os.getenv("SMTP_USER") or None
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or None
SMTP_FROM = os.getenv("SMTP_FROM") or SMTP_USER

EMAIL_ENABLED = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def parse_days(csv: str) -> list[int]:
    """Parse a comma-separated list of ints, e.g. '7,1,0' -> [7, 1, 0]."""
    out = []
    for part in csv.split(","):
        part = part.strip()
        if part == "":
            continue
        out.append(int(part))
    return out
