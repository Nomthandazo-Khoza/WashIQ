"""
SMTP email sending with graceful fallback when not configured.
Uses only the Python standard library (smtplib).
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.services.messaging_config import load_smtp_settings

logger = logging.getLogger("washiq.messaging.email")


def send_email(to_address: str, subject: str, body: str) -> tuple[bool, str | None]:
    """
    Attempt real SMTP send when SMTP_HOST + SMTP_FROM_EMAIL are set.
    Returns (success, error_message_or_none).
    """
    cfg = load_smtp_settings()
    to_address = (to_address or "").strip()
    if not to_address:
        return False, "empty_recipient"

    if not cfg.enabled:
        return False, "not_configured"

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.from_email
        msg["To"] = to_address
        msg.set_content(body)

        with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as smtp:
            if cfg.use_tls:
                smtp.starttls()
            if cfg.username:
                smtp.login(cfg.username, cfg.password)
            smtp.send_message(msg)
        return True, None
    except Exception as exc:
        logger.warning("SMTP send failed for %s: %s", to_address, exc)
        return False, str(exc)
