import math
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import auth_template_context, get_current_customer, is_admin_customer
from app.database import get_db
from app.models import Booking, Customer, Payment

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _money_int_str(value: object) -> str:
    """Safe whole-rand display; avoids Jinja `|int` crashing on inf/nan."""
    try:
        x = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if not math.isfinite(x):
        return "0"
    return str(int(round(x)))


def _payment_status_key(status: object) -> str:
    """Lowercase slug for CSS classes and comparisons (handles None / odd values)."""
    if status is None:
        return "unknown"
    raw = str(status).strip().lower() or "unknown"
    return raw.replace(" ", "-").replace("_", "-")


def _admin_redirect(request: Request, db: Session) -> RedirectResponse | None:
    """Return a redirect if the user is not an authenticated admin."""
    current_customer = get_current_customer(request, db)
    if not current_customer:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if not is_admin_customer(current_customer):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return None


def _build_admin_context(
    request: Request,
    db: Session,
    admin_section: str,
    **extra,
) -> dict:
    ctx = {"request": request, "admin_section": admin_section, **extra}
    ctx.update(auth_template_context(request, db))
    return ctx


def _load_metrics(db: Session) -> dict:
    total_bookings = db.query(func.count(Booking.id)).scalar() or 0
    paid_bookings = (
        db.query(func.count(Booking.id))
        .filter(func.lower(Booking.payment_status) == "paid")
        .scalar()
        or 0
    )
    unpaid_bookings = total_bookings - paid_bookings
    total_revenue = (
        db.query(func.coalesce(func.sum(Booking.estimated_price), 0))
        .filter(func.lower(Booking.payment_status) == "paid")
        .scalar()
        or 0
    )
    tr = float(total_revenue or 0)
    if not math.isfinite(tr):
        tr = 0.0
    return {
        "total_bookings": total_bookings,
        "paid_bookings": paid_bookings,
        "unpaid_bookings": unpaid_bookings,
        "total_revenue": tr,
        "total_revenue_display": _money_int_str(tr),
    }


def _booking_rows(db: Session, limit: int | None = None) -> list[dict]:
    # Prefer id ordering so older SQLite DBs without `created_at` still work.
    q = (
        db.query(Booking, Customer.full_name)
        .outerjoin(Customer, Booking.customer_id == Customer.id)
        .order_by(Booking.id.desc())
    )
    if limit is not None:
        q = q.limit(limit)
    rows = q.all()
    out: list[dict] = []
    for booking, full_name in rows:
        out.append(
            {
                "id": booking.id,
                "customer_name": full_name or "Guest Booking",
                "vehicle": f"{booking.car_model} - {booking.registration_number}",
                "service": booking.service,
                "booking_date": booking.booking_date,
                "time_slot": booking.time_slot,
                "amount": booking.estimated_price,
                "amount_display": _money_int_str(booking.estimated_price),
                "payment_status": booking.payment_status,
                "payment_status_key": _payment_status_key(booking.payment_status),
                "status": booking.status,
            }
        )
    return out


def _load_alerts(db: Session) -> list[str]:
    alerts: list[str] = []
    unpaid_rows = (
        db.query(Booking, Customer.full_name)
        .outerjoin(Customer, Booking.customer_id == Customer.id)
        .filter(func.lower(Booking.payment_status) != "paid")
        .order_by(Booking.booking_date.desc(), Booking.time_slot.desc())
        .limit(6)
        .all()
    )
    for booking, full_name in unpaid_rows:
        alerts.append(
            f"Outstanding payment for {booking.service} on {booking.booking_date} at {booking.time_slot} ({full_name or 'Guest'})."
        )
    return alerts


@router.get("/dashboard")
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    context = _build_admin_context(
        request,
        db,
        "dashboard",
        metrics=_load_metrics(db),
        recent_bookings=_booking_rows(db, limit=10),
        alerts=_load_alerts(db),
    )
    return templates.TemplateResponse(request, "admin/dashboard.html", context)


@router.get("/dashboard/bookings")
def admin_bookings_page(request: Request, db: Session = Depends(get_db)):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    context = _build_admin_context(
        request,
        db,
        "bookings",
        bookings=_booking_rows(db, limit=None),
    )
    return templates.TemplateResponse(request, "admin/bookings.html", context)


@router.get("/dashboard/payments")
def admin_payments_page(request: Request, db: Session = Depends(get_db)):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    context = _build_admin_context(request, db, "payments")
    return templates.TemplateResponse(request, "admin/payments.html", context)


@router.get("/dashboard/customers")
def admin_customers_page(request: Request, db: Session = Depends(get_db)):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    context = _build_admin_context(request, db, "customers")
    return templates.TemplateResponse(request, "admin/customers.html", context)


@router.get("/dashboard/vehicles")
def admin_vehicles_page(request: Request, db: Session = Depends(get_db)):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    context = _build_admin_context(request, db, "vehicles")
    return templates.TemplateResponse(request, "admin/vehicles.html", context)


@router.get("/dashboard/reports")
def admin_reports_page(request: Request, db: Session = Depends(get_db)):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    context = _build_admin_context(request, db, "reports")
    return templates.TemplateResponse(request, "admin/reports.html", context)


@router.get("/dashboard/settings")
def admin_settings_page(request: Request, db: Session = Depends(get_db)):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    context = _build_admin_context(request, db, "settings")
    return templates.TemplateResponse(request, "admin/settings.html", context)


def _require_admin(request: Request, db: Session) -> Customer | None:
    current_customer = get_current_customer(request, db)
    if not current_customer:
        return None
    if not is_admin_customer(current_customer):
        return None
    return current_customer


@router.post("/dashboard/booking/{booking_id}/mark-paid")
def mark_booking_paid(
    booking_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        return RedirectResponse(url="/dashboard/bookings", status_code=status.HTTP_303_SEE_OTHER)

    try:
        raw_amt = float(booking.estimated_price or 0)
    except (TypeError, ValueError):
        raw_amt = 0.0
    if not math.isfinite(raw_amt):
        raw_amt = 0.0
    payment = Payment(
        booking_id=booking.id,
        method="Card",
        amount=raw_amt,
        status="paid",
        provider="manual",
        provider_reference=f"MANUAL-{datetime.now().strftime('%Y%m%d%H%M%S')}",
    )
    db.add(payment)
    booking.payment_status = "paid"
    db.commit()

    return RedirectResponse(url="/dashboard/bookings", status_code=status.HTTP_303_SEE_OTHER)
