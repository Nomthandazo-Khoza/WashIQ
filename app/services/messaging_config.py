"""
Environment-driven messaging configuration. No secrets hardcoded.
See README for SMTP_* and SMS_* variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str
    from_email: str
    use_tls: bool

    @property
    def enabled(self) -> bool:
        return bool(self.host.strip() and self.from_email.strip())


def load_smtp_settings() -> SmtpSettings:
    port_raw = os.environ.get("SMTP_PORT", "587")
    try:
        port = int(port_raw)
    except ValueError:
        port = 587
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes", "on")
    return SmtpSettings(
        host=(os.environ.get("SMTP_HOST") or "").strip(),
        port=port,
        username=(os.environ.get("SMTP_USERNAME") or "").strip(),
        password=os.environ.get("SMTP_PASSWORD") or "",
        from_email=(os.environ.get("SMTP_FROM_EMAIL") or "").strip(),
        use_tls=use_tls,
    )


def sms_configured() -> bool:
    key = (os.environ.get("SMS_API_KEY") or "").strip()
    webhook = (os.environ.get("SMS_WEBHOOK_URL") or "").strip()
    return bool(key and webhook)


def sms_sender_id() -> str:
    return (os.environ.get("SMS_SENDER_ID") or "WashIQ").strip() or "WashIQ"
