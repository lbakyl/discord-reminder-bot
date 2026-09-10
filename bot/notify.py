"""Email notifications via SMTP. Only used when SMTP_* config is present."""
import asyncio
import smtplib
from email.mime.text import MIMEText

from . import config


def _send_sync(subject: str, body: str, to_addrs: list[str]) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = ", ".join(to_addrs)

    if config.SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
        server.starttls()

    try:
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.sendmail(config.SMTP_FROM, to_addrs, msg.as_string())
    finally:
        server.quit()


async def send_email(subject: str, body: str, to_addrs: list[str]) -> None:
    if not config.EMAIL_ENABLED:
        raise RuntimeError("Email is not configured (SMTP_HOST/SMTP_USER/SMTP_PASSWORD missing)")
    if not to_addrs:
        return
    await asyncio.to_thread(_send_sync, subject, body, to_addrs)
