"""Customer portal pages (dashboard, my bookings, profile) — separate from admin /dashboard."""

from pathlib import Path
from typing import Annotated, Any, Optional, Tuple

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import auth_template_context, get_current_customer, is_admin_customer
from app.database import get_db
from app.feedback_helpers import format_feedback_date
from app.models import Booking, Customer, Feedback, Payment

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def customer_payment_nav_href(db: Session, customer_id: int) -> str:
    """Sidebar Payments link: first unpaid booking if any, else /payment."""
    row = (
        db.query(Booking.id)
        .filter(Booking.customer_id == customer_id)
        .filter(func.lower(Booking.payment_status) != "paid")
        .order_by(Booking.id.desc())
        .first()
    )
    if row:
        return f"/payment?booking_id={row[0]}"
    return "/payment"


def attach_customer_sidebar_nav(
    db: Session,
    request: Request,
    context: dict,
    customer_section: str,
) -> None:
    """Add sidebar active state + Payments href for booking/payment templates."""
    cur = get_current_customer(request, db)
    if cur and not is_admin_customer(cur):
        context["customer_section"] = customer_section
        context["payment_nav_href"] = customer_payment_nav_href(db, cur.id)


def _customer_portal_guard(
    request: Request, db: Session
) -> Tuple[Optional[Customer], Optional[RedirectResponse]]:
    current = get_current_customer(request, db)
    if not current:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if is_admin_customer(current):
        return None, RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return current, None


def _load_customer_bookings(db: Session, customer_id: int):
    return (
        db.query(Booking)
        .filter(Booking.customer_id == customer_id)
        .order_by(Booking.id.desc())
        .all()
    )


def _booking_counts(bookings) -> Tuple[int, int, int]:
    total = len(bookings)
    paid = sum(1 for b in bookings if (b.payment_status or "").lower() == "paid")
    return total, paid, total - paid


COMMENT_MAX_LEN = 2000


def _booking_options_for_feedback(db: Session, customer_id: int) -> list[dict[str, Any]]:
    bookings = (
        db.query(Booking)
        .filter(Booking.customer_id == customer_id)
        .order_by(Booking.id.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": b.id,
            "label": f"#{b.id} — {b.service} on {b.booking_date} ({b.time_slot})",
        }
        for b in bookings
    ]


def _serialize_my_feedback(db: Session, customer_id: int) -> list[dict[str, Any]]:
    rows = (
        db.query(Feedback)
        .filter(Feedback.customer_id == customer_id)
        .order_by(Feedback.created_at.desc())
        .all()
    )
    out: list[dict[str, Any]] = []
    for fb in rows:
        booking_label = "General feedback"
        if fb.booking_id:
            b = db.query(Booking).filter(Booking.id == fb.booking_id).first()
            if b:
                booking_label = f"Booking #{b.id} · {b.service}"
        out.append(
            {
                "rating": fb.rating,
                "comment": (fb.comment or "").strip(),
                "booking_label": booking_label,
                "date_label": format_feedback_date(fb.created_at),
            }
        )
    return out


def _feedback_page_context(
    request: Request,
    db: Session,
    customer: Customer,
    *,
    form_error: str | None = None,
    form_rating: int | None = None,
    form_booking_id: str = "",
    form_comment: str = "",
    feedback_submitted: bool = False,
) -> dict[str, Any]:
    return {
        "request": request,
        "customer_section": "feedback",
        "payment_nav_href": customer_payment_nav_href(db, customer.id),
        "feedback_submitted": feedback_submitted,
        "form_error": form_error,
        "form_rating": form_rating,
        "form_booking_id": form_booking_id,
        "form_comment": form_comment,
        "my_feedback": _serialize_my_feedback(db, customer.id),
        "booking_options": _booking_options_for_feedback(db, customer.id),
    }


@router.get("/customer")
def customer_dashboard_page(request: Request, db: Session = Depends(get_db)):
    customer, redir = _customer_portal_guard(request, db)
    if redir:
        return redir

    bookings = _load_customer_bookings(db, customer.id)
    total_b, paid_b, pending_b = _booking_counts(bookings)

    context = {
        "request": request,
        "customer_section": "dashboard",
        "payment_nav_href": customer_payment_nav_href(db, customer.id),
        "bookings": bookings,
        "total_bookings": total_b,
        "paid_bookings": paid_b,
        "pending_bookings": pending_b,
    }
    context.update(auth_template_context(request, db))
    return templates.TemplateResponse(request, "customer/dashboard.html", context)


@router.get("/my-bookings")
def my_bookings_page(request: Request, db: Session = Depends(get_db)):
    customer, redir = _customer_portal_guard(request, db)
    if redir:
        return redir

    bookings = _load_customer_bookings(db, customer.id)
    booking_receipts: dict[int, int] = {}
    for b in bookings:
        if (b.payment_status or "").lower() == "paid":
            pay = (
                db.query(Payment)
                .filter(Payment.booking_id == b.id)
                .order_by(Payment.id.desc())
                .first()
            )
            if pay:
                booking_receipts[b.id] = pay.id
    context = {
        "request": request,
        "customer_section": "bookings",
        "payment_nav_href": customer_payment_nav_href(db, customer.id),
        "bookings": bookings,
        "booking_receipts": booking_receipts,
    }
    context.update(auth_template_context(request, db))
    return templates.TemplateResponse(request, "customer/my_bookings.html", context)


@router.get("/profile")
def customer_profile_page(request: Request, db: Session = Depends(get_db)):
    customer, redir = _customer_portal_guard(request, db)
    if redir:
        return redir

    context = {
        "request": request,
        "customer_section": "profile",
        "payment_nav_href": customer_payment_nav_href(db, customer.id),
    }
    context.update(auth_template_context(request, db))
    return templates.TemplateResponse(request, "customer/profile_account.html", context)


@router.get("/feedback")
def feedback_page(
    request: Request,
    db: Session = Depends(get_db),
    submitted: Annotated[str | None, Query()] = None,
):
    customer, redir = _customer_portal_guard(request, db)
    if redir:
        return redir

    context = _feedback_page_context(
        request,
        db,
        customer,
        feedback_submitted=(submitted == "1"),
    )
    context.update(auth_template_context(request, db))
    return templates.TemplateResponse(request, "customer/feedback.html", context)


@router.post("/feedback")
def feedback_submit(
    request: Request,
    rating: str = Form(""),
    booking_id: str = Form(""),
    comment: str = Form(""),
    db: Session = Depends(get_db),
):
    customer, redir = _customer_portal_guard(request, db)
    if redir:
        return redir

    def err(
        msg: str,
        *,
        form_comment_e: str = "",
        form_booking_id_e: str = "",
        form_rating_e: int | None = None,
    ) -> Any:
        ctx = _feedback_page_context(
            request,
            db,
            customer,
            form_error=msg,
            form_booking_id=form_booking_id_e,
            form_comment=form_comment_e,
            form_rating=form_rating_e,
        )
        ctx.update(auth_template_context(request, db))
        return templates.TemplateResponse(request, "customer/feedback.html", ctx)

    comment_t = (comment or "").strip()
    if len(comment_t) > COMMENT_MAX_LEN:
        return err(
            f"Your comment is too long (maximum {COMMENT_MAX_LEN} characters).",
            form_comment_e=comment_t[:COMMENT_MAX_LEN],
            form_booking_id_e=(booking_id or "").strip(),
            form_rating_e=None,
        )

    raw_rating = (rating or "").strip()
    if not raw_rating:
        return err(
            "Please choose a rating from 1 to 5.",
            form_comment_e=comment_t,
            form_booking_id_e=(booking_id or "").strip(),
            form_rating_e=None,
        )
    try:
        rating_val = int(raw_rating)
    except ValueError:
        return err(
            "Please choose a valid rating.",
            form_comment_e=comment_t,
            form_booking_id_e=(booking_id or "").strip(),
            form_rating_e=None,
        )
    if rating_val < 1 or rating_val > 5:
        return err(
            "Rating must be between 1 and 5 stars.",
            form_comment_e=comment_t,
            form_booking_id_e=(booking_id or "").strip(),
            form_rating_e=rating_val,
        )

    bid: int | None = None
    raw_b = (booking_id or "").strip()
    if raw_b:
        try:
            bid = int(raw_b)
        except ValueError:
            return err(
                "Invalid booking selected.",
                form_comment_e=comment_t,
                form_booking_id_e="",
                form_rating_e=rating_val,
            )
        booking = (
            db.query(Booking)
            .filter(Booking.id == bid, Booking.customer_id == customer.id)
            .first()
        )
        if not booking:
            return err(
                "That booking is not linked to your account.",
                form_comment_e=comment_t,
                form_booking_id_e="",
                form_rating_e=rating_val,
            )
        dup = (
            db.query(Feedback)
            .filter(Feedback.customer_id == customer.id, Feedback.booking_id == bid)
            .first()
        )
        if dup:
            return err(
                "You’ve already submitted feedback for that booking.",
                form_comment_e=comment_t,
                form_booking_id_e=str(bid),
                form_rating_e=rating_val,
            )

    entry = Feedback(
        customer_id=customer.id,
        booking_id=bid,
        rating=rating_val,
        comment=comment_t if comment_t else None,
    )
    db.add(entry)
    db.commit()

    return RedirectResponse(url="/feedback?submitted=1", status_code=status.HTTP_303_SEE_OTHER)
