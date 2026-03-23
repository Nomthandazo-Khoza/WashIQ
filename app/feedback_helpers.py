"""Helpers for WashIQ customer feedback (Phase B) — display names and shared queries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.auth import coerce_is_admin
from app.models import Booking, Customer, Feedback


def public_testimonial_name(full_name: str | None) -> str:
    """First name + last initial for privacy (e.g. 'Ayanda M.')."""
    parts = (full_name or "").strip().split()
    if not parts:
        return "Customer"
    if len(parts) == 1:
        return parts[0]
    initial = parts[-1][0].upper() if parts[-1] else ""
    return f"{parts[0]} {initial}." if initial else parts[0]


def format_feedback_date(value: datetime | None) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%d %b %Y")
        except (ValueError, OSError):
            return str(value)[:10]
    return str(value)[:10]


def home_testimonials(db: Session, limit: int = 3) -> list[dict[str, Any]]:
    """Recent feedback from non-admin customers for the public homepage."""
    rows = (
        db.query(Feedback, Customer)
        .join(Customer, Feedback.customer_id == Customer.id)
        .order_by(Feedback.created_at.desc())
        .limit(limit * 8)
        .all()
    )
    out: list[dict[str, Any]] = []
    for fb, cust in rows:
        if coerce_is_admin(cust.is_admin):
            continue
        comment = (fb.comment or "").strip()
        if not comment:
            # Star-only reviews: show a short default line for homepage cards
            comment = "Great service — highly recommend WashIQ."
        out.append(
            {
                "display_name": public_testimonial_name(cust.full_name),
                "rating": fb.rating,
                "comment": comment,
                "date_label": format_feedback_date(fb.created_at),
            }
        )
        if len(out) >= limit:
            break
    return out


def _feedback_row_dict(fb: Feedback, cust: Customer, booking: Booking | None) -> dict[str, Any]:
    booking_label = ""
    if booking:
        booking_label = f"#{booking.id} · {booking.service} · {booking.booking_date}"
    r = int(fb.rating)
    stars_display = "*" * r + "-" * (5 - r)
    return {
        "id": fb.id,
        "customer_name": cust.full_name or "—",
        "customer_email": cust.email or "—",
        "rating": fb.rating,
        "stars_display": stars_display,
        "comment": (fb.comment or "").strip() or "—",
        "booking_id": fb.booking_id,
        "booking_label": booking_label or "—",
        "created_at_label": format_feedback_date(fb.created_at),
    }


def admin_feedback_rows(db: Session, limit: int = 25) -> list[dict[str, Any]]:
    """Feedback list for admin dashboard widget."""
    rows = (
        db.query(Feedback, Customer, Booking)
        .join(Customer, Feedback.customer_id == Customer.id)
        .outerjoin(Booking, Feedback.booking_id == Booking.id)
        .order_by(Feedback.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_feedback_row_dict(fb, cust, booking) for fb, cust, booking in rows]


def feedback_admin_metrics(db: Session) -> dict[str, Any]:
    """Summary metrics for /dashboard/feedback."""
    total = db.query(func.count(Feedback.id)).scalar() or 0
    avg = average_feedback_rating(db)
    five_star = db.query(func.count(Feedback.id)).filter(Feedback.rating == 5).scalar() or 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent = db.query(func.count(Feedback.id)).filter(Feedback.created_at >= cutoff).scalar() or 0
    return {
        "total": int(total),
        "average": avg,
        "average_display": f"{avg:.2f}" if avg is not None else "—",
        "five_star": int(five_star),
        "recent_30d": int(recent),
    }


def admin_feedback_rows_filtered(
    db: Session,
    *,
    rating: int | None = None,
    search: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Admin feedback table with optional rating + customer name search."""
    q = (
        db.query(Feedback, Customer, Booking)
        .join(Customer, Feedback.customer_id == Customer.id)
        .outerjoin(Booking, Feedback.booking_id == Booking.id)
    )
    if rating is not None and 1 <= rating <= 5:
        q = q.filter(Feedback.rating == rating)
    search_t = (search or "").strip()
    if search_t:
        like = f"%{search_t.lower()}%"
        q = q.filter(
            or_(
                func.lower(Customer.full_name).like(like),
                func.lower(Customer.email).like(like),
            )
        )
    rows = q.order_by(Feedback.created_at.desc()).limit(limit).all()
    return [_feedback_row_dict(fb, cust, booking) for fb, cust, booking in rows]


def average_feedback_rating(db: Session) -> float | None:
    """Simple average for admin headline (None if no rows)."""
    n = db.query(func.count(Feedback.id)).scalar() or 0
    if n == 0:
        return None
    total = db.query(func.coalesce(func.sum(Feedback.rating), 0)).scalar() or 0
    try:
        return round(float(total) / float(n), 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
