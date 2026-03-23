"""WashIQ promotions (Phase C) — homepage selection and admin helpers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Promotion


def truthy_active(val: object) -> bool:
    """Normalize SQLite / form boolean-ish values."""
    if val is True or val == 1:
        return True
    if val is False or val is None or val == 0:
        return False
    if isinstance(val, str) and val.strip().lower() in ("1", "true", "yes", "on"):
        return True
    return bool(val)


def in_promotion_date_window(p: Promotion, today: date) -> bool:
    """True if today falls in optional [start_date, end_date] (inclusive)."""
    if p.start_date is not None and today < p.start_date:
        return False
    if p.end_date is not None and today > p.end_date:
        return False
    return True


def promotion_applies_on_date(p: Promotion, today: date) -> bool:
    """True if row is marked active and today falls in optional [start_date, end_date]."""
    return truthy_active(p.active) and in_promotion_date_window(p, today)


def pick_homepage_promotion_row(db: Session, today: date | None = None) -> Promotion | None:
    """
    The single row shown on the homepage: newest by created_at (then id) that passes
    active + date window rules.
    """
    today = today or date.today()
    rows = (
        db.query(Promotion)
        .order_by(Promotion.created_at.desc(), Promotion.id.desc())
        .all()
    )
    for p in rows:
        if promotion_applies_on_date(p, today):
            return p
    return None


def get_homepage_promotion(db: Session, today: date | None = None) -> dict[str, Any] | None:
    """Public homepage payload for the winning promotion, if any."""
    p = pick_homepage_promotion_row(db, today)
    if not p:
        return None
    cta_link = (p.cta_link or "").strip()
    cta_text = (p.cta_text or "").strip() or "Book now"
    badge = (p.badge_text or "").strip() or None
    return {
        "id": p.id,
        "title": p.title,
        "description": p.description,
        "badge_text": badge,
        "cta_text": cta_text,
        "cta_link": cta_link if cta_link else None,
    }


def parse_optional_date(raw: str | None, label: str) -> tuple[date | None, str | None]:
    """
    Parse YYYY-MM-DD or empty. Returns (value, error_message).
    """
    s = (raw or "").strip()
    if not s:
        return None, None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date(), None
    except ValueError:
        return None, f"Invalid {label} — use YYYY-MM-DD."


def promotion_admin_summary(db: Session, today: date | None = None) -> dict[str, Any]:
    """Metrics for the admin promotions dashboard (top cards)."""
    today = today or date.today()
    rows = db.query(Promotion).all()
    total = len(rows)
    active_n = sum(1 for p in rows if truthy_active(p.active))
    eligible_n = sum(1 for p in rows if promotion_applies_on_date(p, today))
    scheduled_n = sum(
        1
        for p in rows
        if truthy_active(p.active) and p.start_date is not None and p.start_date > today
    )
    winner = pick_homepage_promotion_row(db, today)
    return {
        "total": total,
        "active_count": active_n,
        "scheduled_count": scheduled_n,
        "eligible_today_count": eligible_n,
        "live_id": winner.id if winner else None,
        "live_title": winner.title if winner else None,
    }


def _homepage_status_for_row(
    p: Promotion,
    *,
    today: date,
    winner_id: int | None,
) -> tuple[str, str]:
    """
    Return (css_key, label) for how this row relates to the homepage strip.
    """
    active_b = truthy_active(p.active)
    in_window = in_promotion_date_window(p, today)
    applies = active_b and in_window

    if winner_id is not None and p.id == winner_id:
        return "live", "Live on homepage"
    if applies:
        return "queued", "Eligible — newer promo shown"
    if not active_b:
        return "off", "Inactive"
    if active_b and not in_window:
        return "outside", "Outside date window"
    return "off", "—"


def promotion_rows_for_admin(db: Session, today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    winner = pick_homepage_promotion_row(db, today)
    winner_id = winner.id if winner else None

    rows = (
        db.query(Promotion)
        .order_by(Promotion.created_at.desc(), Promotion.id.desc())
        .all()
    )
    out: list[dict[str, Any]] = []
    for p in rows:
        desc = p.description or ""
        short = desc if len(desc) <= 100 else desc[:97] + "…"
        hs_key, hs_label = _homepage_status_for_row(p, today=today, winner_id=winner_id)
        created = p.created_at
        created_label = created.strftime("%Y-%m-%d %H:%M") if created else "—"
        badge = (p.badge_text or "").strip() or "—"
        raw_cta = (p.cta_text or "").strip()
        cta_text_short = raw_cta[:60] + ("…" if len(raw_cta) > 60 else "") if raw_cta else "—"
        cta_l = (p.cta_link or "").strip()
        cta_link_short = (cta_l[:48] + "…") if len(cta_l) > 48 else (cta_l or "—")

        out.append(
            {
                "id": p.id,
                "title": p.title,
                "description_short": short,
                "active": truthy_active(p.active),
                "start_date": p.start_date,
                "end_date": p.end_date,
                "live_on_homepage": hs_key == "live",
                "homepage_status_key": hs_key,
                "homepage_status_label": hs_label,
                "badge_text": badge,
                "cta_text_short": cta_text_short,
                "cta_link_short": cta_link_short,
                "cta_link": cta_l or None,
                "created_at": p.created_at,
                "created_at_label": created_label,
            }
        )
    return out
