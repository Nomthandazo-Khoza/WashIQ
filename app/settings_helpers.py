"""Single-row app settings for admin (Phase 5)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.contact_info import ADDRESS_LINES, EMAIL, OPERATING_HOURS, PHONE_DISPLAY, PHONE_E164_DIGITS
from app.models import AppSettings

SETTINGS_ROW_ID = 1


def _default_address_text() -> str:
    return "\n".join(ADDRESS_LINES)


def _default_hours_text() -> str:
    lines = [f"{row['label']}: {row['value']}" for row in OPERATING_HOURS]
    return "\n".join(lines)


def get_or_create_app_settings(db: Session) -> AppSettings:
    row = db.query(AppSettings).filter(AppSettings.id == SETTINGS_ROW_ID).first()
    if row:
        return row
    row = AppSettings(
        id=SETTINGS_ROW_ID,
        business_name="WashIQ",
        support_email=EMAIL,
        contact_phone=PHONE_DISPLAY,
        whatsapp_e164=PHONE_E164_DIGITS.lstrip("+"),
        address_text=_default_address_text(),
        operating_hours_text=_default_hours_text(),
        receipt_footer_note="Thank you for choosing WashIQ.",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def receipt_footer_from_settings(db: Session) -> str:
    """Short line for receipt template; safe if table missing (should not happen)."""
    try:
        s = get_or_create_app_settings(db)
        return (s.receipt_footer_note or "").strip() or "Thank you for choosing WashIQ."
    except Exception:
        return "Thank you for choosing WashIQ."
