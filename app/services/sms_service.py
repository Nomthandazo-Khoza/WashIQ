"""
SMS delivery stub: structured for future Twilio/http providers.
When SMS_API_KEY is not set, callers should treat as simulated (see communication_service).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger("washiq.messaging.sms")


def send_sms_http_webhook(to_e164: str, body: str) -> tuple[bool, str | None]:
    """
    Optional generic webhook: POST JSON to SMS_WEBHOOK_URL if set.
    Body: {"to": "...", "message": "...", "sender": "..."}
    """
    url = (os.environ.get("SMS_WEBHOOK_URL") or "").strip()
    if not url:
        return False, "no_webhook"

    payload = json.dumps(
        {
            "to": to_e164,
            "message": body,
            "sender": (os.environ.get("SMS_SENDER_ID") or "WashIQ").strip(),
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = (os.environ.get("SMS_API_KEY") or "").strip()
    if api_key:
        headers["X-API-Key"] = api_key

    req = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if 200 <= resp.status < 300:
                return True, None
            return False, f"http_{resp.status}"
    except urllib.error.URLError as exc:
        logger.warning("SMS webhook failed: %s", exc)
        return False, str(exc)


def send_sms(to_phone: str, body: str) -> tuple[bool, str | None]:
    """
    MVP: if SMS_WEBHOOK_URL + SMS_API_KEY set, POST to webhook.
    Otherwise returns (False, 'not_configured') — communication_service logs as simulated.
    """
    to_phone = (to_phone or "").strip()
    if not to_phone:
        return False, "empty_recipient"

    api_key = (os.environ.get("SMS_API_KEY") or "").strip()
    webhook = (os.environ.get("SMS_WEBHOOK_URL") or "").strip()
    if api_key and webhook:
        return send_sms_http_webhook(to_phone, body[:480])

    return False, "not_configured"
