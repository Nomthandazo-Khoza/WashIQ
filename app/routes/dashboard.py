import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.auth import auth_template_context, get_current_customer, is_admin_customer
from app.database import get_db
from app.feedback_helpers import (
    admin_feedback_rows,
    admin_feedback_rows_filtered,
    average_feedback_rating,
    feedback_admin_metrics,
)
from app.models import AppSettings, Booking, CommunicationLog, Customer, Payment, Promotion
from app.settings_helpers import get_or_create_app_settings
from app.promotion_helpers import (
    parse_optional_date,
    promotion_admin_summary,
    promotion_rows_for_admin,
    truthy_active,
)
from app.routes.payment import METHOD_LABELS
from app.services.communication_service import recent_communication_logs

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


def _week_bounds(today: date) -> tuple[date, date]:
    """Monday–Sunday week containing `today` (calendar week, ISO-style Monday start)."""
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    return start, end


def _customer_return_rate_stats(db: Session) -> tuple[int, int, int]:
    """
    Return (returning_customers_count, customers_with_any_booking_count, rate_percent).
    Returning = more than one booking rows for same customer_id (non-null).
    """
    rows = (
        db.query(Booking.customer_id, func.count(Booking.id))
        .filter(Booking.customer_id.isnot(None))
        .group_by(Booking.customer_id)
        .all()
    )
    total_with = len(rows)
    returning = sum(1 for _cid, cnt in rows if cnt > 1)
    if total_with == 0:
        return 0, 0, 0
    pct = int(round(100.0 * returning / total_with))
    return returning, total_with, pct


def _load_metrics(db: Session) -> dict:
    today = date.today()
    week_start, week_end = _week_bounds(today)

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

    bookings_today = (
        db.query(func.count(Booking.id)).filter(Booking.booking_date == today).scalar() or 0
    )
    bookings_this_week = (
        db.query(func.count(Booking.id))
        .filter(Booking.booking_date >= week_start)
        .filter(Booking.booking_date <= week_end)
        .scalar()
        or 0
    )

    returning_n, with_bookings_n, return_rate_pct = _customer_return_rate_stats(db)

    return {
        "total_bookings": total_bookings,
        "paid_bookings": paid_bookings,
        "unpaid_bookings": unpaid_bookings,
        "total_revenue": tr,
        "total_revenue_display": _money_int_str(tr),
        "bookings_today": bookings_today,
        "bookings_this_week": bookings_this_week,
        "week_label": f"{week_start.isoformat()} to {week_end.isoformat()}",
        "return_rate_pct": return_rate_pct,
        "return_rate_returning": returning_n,
        "return_rate_customers": with_bookings_n,
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
                "collection_status": getattr(booking, "collection_status", None) or "not_applicable",
            }
        )
    return out


def _reports_page_data(db: Session) -> dict:
    """Server-rendered business summaries for /dashboard/reports."""
    m = _load_metrics(db)
    returning_n, with_bookings_n, return_rate_pct = _customer_return_rate_stats(db)

    rev_rows: list[dict] = []
    for svc, amt in (
        db.query(Booking.service, func.coalesce(func.sum(Booking.estimated_price), 0))
        .filter(func.lower(Booking.payment_status) == "paid")
        .group_by(Booking.service)
        .order_by(func.coalesce(func.sum(Booking.estimated_price), 0).desc())
        .all()
    ):
        rev_rows.append({"service": svc, "amount_display": _money_int_str(amt)})

    book_rows: list[dict] = []
    for svc, cnt in (
        db.query(Booking.service, func.count(Booking.id))
        .group_by(Booking.service)
        .order_by(func.count(Booking.id).desc())
        .all()
    ):
        book_rows.append({"service": svc, "count": int(cnt)})

    top_by_bookings: list[dict] = []
    for name, cnt in (
        db.query(Customer.full_name, func.count(Booking.id))
        .join(Booking, Customer.id == Booking.customer_id)
        .group_by(Customer.id)
        .order_by(func.count(Booking.id).desc())
        .limit(8)
        .all()
    ):
        top_by_bookings.append({"name": name, "count": int(cnt)})

    top_by_spend: list[dict] = []
    for name, sp in (
        db.query(Customer.full_name, func.coalesce(func.sum(Payment.amount), 0))
        .join(Payment, Customer.id == Payment.customer_id)
        .filter(func.lower(Payment.status) == "paid")
        .filter(Payment.customer_id.isnot(None))
        .group_by(Customer.id)
        .order_by(func.sum(Payment.amount).desc())
        .limit(8)
        .all()
    ):
        top_by_spend.append({"name": name, "spend_display": _money_int_str(sp)})

    top_service = book_rows[0]["service"] if book_rows else "—"
    top_service_count = book_rows[0]["count"] if book_rows else 0

    return {
        "reports_metrics": m,
        "return_rate_pct": return_rate_pct,
        "return_rate_returning": returning_n,
        "return_rate_customers": with_bookings_n,
        "revenue_by_service": rev_rows,
        "bookings_by_service": book_rows,
        "top_customers_by_bookings": top_by_bookings,
        "top_customers_by_spend": top_by_spend,
        "top_service": top_service,
        "top_service_count": top_service_count,
    }


def _vehicle_fleet_rows(db: Session, *, search: str = "") -> tuple[list[dict], dict]:
    """Distinct vehicles from bookings (reg + model + customer), with counts."""
    today = date.today()
    search_t = (search or "").strip()
    q = (
        db.query(
            Booking.registration_number,
            Booking.car_model,
            Booking.customer_id,
            func.count(Booking.id).label("bc"),
            func.max(Booking.booking_date).label("last_dt"),
        )
        .outerjoin(Customer, Booking.customer_id == Customer.id)
        .group_by(Booking.registration_number, Booking.car_model, Booking.customer_id)
    )
    if search_t:
        like = f"%{search_t.lower()}%"
        q = q.filter(
            or_(
                func.lower(Booking.registration_number).like(like),
                func.lower(Booking.car_model).like(like),
                func.lower(func.coalesce(Customer.full_name, "")).like(like),
            )
        )
    rows = q.order_by(func.max(Booking.booking_date).desc()).all()

    cust_ids = {cid for _reg, _model, cid, _bc, _ld in rows if cid is not None}
    cust_map: dict[int, str] = {}
    if cust_ids:
        for cid, fn in db.query(Customer.id, Customer.full_name).filter(Customer.id.in_(cust_ids)).all():
            cust_map[int(cid)] = fn or f"Customer #{cid}"

    d30 = today - timedelta(days=30)
    d90 = today - timedelta(days=90)
    active_90 = 0
    recent_30 = 0
    out: list[dict] = []
    for reg, model, cid, bc, last_dt in rows:
        if cid is not None:
            cust_name = cust_map.get(int(cid), f"Customer #{cid}")
        else:
            cust_name = "Guest / walk-in"
        if last_dt and last_dt >= d90:
            active_90 += 1
        if last_dt and last_dt >= d30:
            recent_30 += 1
        out.append(
            {
                "registration_number": reg,
                "car_model": model,
                "customer_name": cust_name,
                "booking_count": int(bc or 0),
                "last_booking_date": last_dt,
                "last_booking_display": last_dt.isoformat() if last_dt else "—",
            }
        )

    metrics = {
        "total_vehicles": len(out),
        "active_90": active_90,
        "recent_30": recent_30,
    }
    return out, metrics


def _communication_metrics(db: Session) -> dict:
    total = db.query(func.count(CommunicationLog.id)).scalar() or 0

    def _status_cnt(s: str) -> int:
        return (
            db.query(func.count(CommunicationLog.id))
            .filter(func.lower(CommunicationLog.status) == s.lower())
            .scalar()
            or 0
        )

    return {
        "total": int(total),
        "sent": _status_cnt("sent"),
        "simulated": _status_cnt("simulated"),
        "failed": _status_cnt("failed"),
    }


_PAYMENT_METHOD_KEYS = frozenset(METHOD_LABELS.keys())


def _normalize_payments_status_filter(raw: str | None) -> str:
    if raw is None or str(raw).strip() == "":
        return "all"
    s = str(raw).strip().lower()
    if s == "all":
        return "all"
    if s in ("paid", "pending", "failed"):
        return s
    return "all"


def _normalize_payments_method_filter(raw: str | None) -> str:
    if raw is None or str(raw).strip() == "":
        return "all"
    m = str(raw).strip().lower()
    if m == "all":
        return "all"
    if m in _PAYMENT_METHOD_KEYS:
        return m
    return "all"


def _format_payment_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    try:
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return str(dt)[:16]


def _load_payment_analytics(db: Session) -> dict:
    """Global payment metrics (not affected by list filters)."""
    total_transactions = db.query(func.count(Payment.id)).scalar() or 0
    paid_count = (
        db.query(func.count(Payment.id)).filter(func.lower(Payment.status) == "paid").scalar() or 0
    )
    pending_count = (
        db.query(func.count(Payment.id)).filter(func.lower(Payment.status) == "pending").scalar() or 0
    )
    revenue = (
        db.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(func.lower(Payment.status) == "paid")
        .scalar()
        or 0
    )
    rev = float(revenue or 0)
    if not math.isfinite(rev):
        rev = 0.0
    return {
        "total_transactions": int(total_transactions),
        "paid_payments": int(paid_count),
        "pending_payments": int(pending_count),
        "total_revenue": rev,
        "total_revenue_display": _money_int_str(rev),
    }


def _payment_history_rows(
    db: Session, *, status_filter: str, method_filter: str
) -> list[dict]:
    q = (
        db.query(Payment, Customer.full_name, Booking.service, Booking.booking_date)
        .outerjoin(Customer, Payment.customer_id == Customer.id)
        .outerjoin(Booking, Payment.booking_id == Booking.id)
    )
    if status_filter != "all":
        q = q.filter(func.lower(Payment.status) == status_filter)
    if method_filter != "all":
        q = q.filter(func.lower(Payment.method) == method_filter)

    rows = q.order_by(Payment.id.desc()).all()
    out: list[dict] = []
    for payment, cust_name, bk_service, bk_date in rows:
        st_key = _payment_status_key(payment.status)
        method_key = (payment.method or "").strip().lower() or "unknown"
        method_label = METHOD_LABELS.get(
            method_key,
            (payment.method or "—").replace("_", " ").strip().title() or "—",
        )
        provider_raw = (payment.provider or "").strip()
        provider_display = provider_raw.replace("_", " ").title() if provider_raw else "—"
        pref = (payment.provider_reference or "").strip()
        pref_display = pref if pref else "—"

        if bk_service and bk_date:
            booking_service_display = f"{bk_service} · {bk_date}"
        elif bk_service:
            booking_service_display = str(bk_service)
        elif payment.booking_id:
            booking_service_display = f"Booking #{payment.booking_id}"
        else:
            booking_service_display = "No linked booking"

        customer_display = (cust_name or "").strip() or "—"
        if customer_display == "—" and payment.customer_id:
            customer_display = f"Customer #{payment.customer_id}"

        out.append(
            {
                "id": payment.id,
                "customer_name": customer_display,
                "booking_service_display": booking_service_display,
                "booking_date": bk_date,
                "amount": payment.amount,
                "amount_display": _money_int_str(payment.amount),
                "method": payment.method,
                "method_label": method_label,
                "status": (payment.status or "—").strip() or "—",
                "status_key": st_key,
                "provider": provider_display,
                "provider_reference": pref_display,
                "created_at_display": _format_payment_datetime(payment.created_at),
            }
        )
    return out


OVERNIGHT_SERVICE = "Overnight Parking"


def _load_dashboard_alerts(db: Session) -> list[dict[str, str]]:
    """
    Operational alerts: unpaid balances (oldest first), then uncollected overnight parking.
    """
    items: list[dict[str, str]] = []
    today = date.today()

    unpaid_rows = (
        db.query(Booking, Customer.full_name)
        .outerjoin(Customer, Booking.customer_id == Customer.id)
        .filter(func.lower(Booking.payment_status) != "paid")
        .order_by(Booking.booking_date.asc(), Booking.time_slot.asc())
        .limit(14)
        .all()
    )
    for booking, full_name in unpaid_rows:
        cust = full_name or "Guest"
        amt = _money_int_str(booking.estimated_price)
        items.append(
            {
                "kind": "unpaid",
                "message": (
                    f"Outstanding payment: {booking.service} for {cust} — "
                    f"{booking.car_model} on {booking.booking_date} (R{amt}). "
                    f"Slot: {booking.time_slot}."
                ),
            }
        )

    abandoned_rows = (
        db.query(Booking, Customer.full_name)
        .outerjoin(Customer, Booking.customer_id == Customer.id)
        .filter(Booking.service == OVERNIGHT_SERVICE)
        .filter(func.lower(Booking.collection_status) != "collected")
        .filter(Booking.booking_date < today)
        .order_by(Booking.booking_date.asc())
        .limit(10)
        .all()
    )
    for booking, full_name in abandoned_rows:
        cust = full_name or "Guest"
        items.append(
            {
                "kind": "abandoned",
                "message": (
                    f"Uncollected overnight parking: {cust} — {booking.car_model} "
                    f"({booking.registration_number}), booked {booking.booking_date}. "
                    f"Confirm vehicle collection or update collection status in your records."
                ),
            }
        )

    return items


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
        alerts=_load_dashboard_alerts(db),
        recent_feedback=admin_feedback_rows(db, limit=8),
        feedback_avg_rating=average_feedback_rating(db),
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
def admin_payments_page(
    request: Request,
    status: str | None = Query(None),
    method: str | None = Query(None),
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    status_filter = _normalize_payments_status_filter(status)
    method_filter = _normalize_payments_method_filter(method)
    analytics = _load_payment_analytics(db)
    payment_rows = _payment_history_rows(db, status_filter=status_filter, method_filter=method_filter)
    filters_active = status_filter != "all" or method_filter != "all"

    context = _build_admin_context(
        request,
        db,
        "payments",
        payment_analytics=analytics,
        payment_rows=payment_rows,
        payments_status_filter=status_filter,
        payments_method_filter=method_filter,
        payments_filters_active=filters_active,
        payments_method_labels=METHOD_LABELS,
        payments_has_any=analytics["total_transactions"] > 0,
    )
    return templates.TemplateResponse(request, "admin/payments.html", context)


def _normalize_customer_segment_filter(raw: str | None) -> str:
    if raw is None or str(raw).strip() == "":
        return "all"
    s = str(raw).strip().lower()
    if s in ("all", "active", "returning"):
        return s
    return "all"


def _customer_analytics(db: Session) -> dict:
    """Global customer metrics (not affected by list search/filters)."""
    total_customers = db.query(func.count(Customer.id)).scalar() or 0

    active_customers = (
        db.query(func.count(func.distinct(Booking.customer_id)))
        .filter(Booking.customer_id.isnot(None))
        .scalar()
        or 0
    )

    booking_counts = (
        db.query(Booking.customer_id, func.count(Booking.id))
        .filter(Booking.customer_id.isnot(None))
        .group_by(Booking.customer_id)
        .all()
    )
    returning_customers = sum(1 for _cid, cnt in booking_counts if cnt > 1)

    revenue = (
        db.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(func.lower(Payment.status) == "paid")
        .filter(Payment.customer_id.isnot(None))
        .scalar()
        or 0
    )
    rev = float(revenue or 0)
    if not math.isfinite(rev):
        rev = 0.0

    return {
        "total_customers": int(total_customers),
        "active_customers": int(active_customers),
        "returning_customers": int(returning_customers),
        "total_customer_revenue": rev,
        "total_customer_revenue_display": _money_int_str(rev),
    }


def _customer_list_subqueries(db: Session):
    paid_booking_expr = case((func.lower(Booking.payment_status) == "paid", 1), else_=0)
    booking_agg = (
        db.query(
            Booking.customer_id.label("cid"),
            func.count(Booking.id).label("total_bookings"),
            func.sum(paid_booking_expr).label("paid_bookings"),
            func.max(Booking.booking_date).label("last_booking_date"),
        )
        .filter(Booking.customer_id.isnot(None))
        .group_by(Booking.customer_id)
        .subquery()
    )
    payment_agg = (
        db.query(
            Payment.customer_id.label("cid"),
            func.coalesce(func.sum(Payment.amount), 0).label("total_spend"),
        )
        .filter(Payment.customer_id.isnot(None))
        .filter(func.lower(Payment.status) == "paid")
        .group_by(Payment.customer_id)
        .subquery()
    )
    return booking_agg, payment_agg


def _customer_list_rows(
    db: Session,
    *,
    search: str,
    segment_filter: str,
) -> list[dict]:
    booking_agg, payment_agg = _customer_list_subqueries(db)
    tb = func.coalesce(booking_agg.c.total_bookings, 0)
    pb = func.coalesce(booking_agg.c.paid_bookings, 0)
    q = (
        db.query(
            Customer,
            tb.label("total_bookings"),
            pb.label("paid_bookings"),
            booking_agg.c.last_booking_date,
            func.coalesce(payment_agg.c.total_spend, 0).label("total_spend"),
        )
        .outerjoin(booking_agg, Customer.id == booking_agg.c.cid)
        .outerjoin(payment_agg, Customer.id == payment_agg.c.cid)
    )

    search_stripped = search.strip()
    if search_stripped:
        like = f"%{search_stripped.lower()}%"
        q = q.filter(
            or_(
                func.lower(Customer.full_name).like(like),
                func.lower(Customer.email).like(like),
            )
        )

    if segment_filter == "active":
        q = q.filter(tb >= 1)
    elif segment_filter == "returning":
        q = q.filter(tb > 1)

    rows = q.order_by(func.lower(Customer.full_name).asc(), Customer.id.asc()).all()

    out: list[dict] = []
    for customer, total_b, paid_b, last_dt, spend in rows:
        tb_i = int(total_b or 0)
        pb_i = int(paid_b or 0)
        spend_f = float(spend or 0)
        if not math.isfinite(spend_f):
            spend_f = 0.0
        out.append(
            {
                "id": customer.id,
                "full_name": customer.full_name,
                "email": customer.email,
                "phone": customer.phone,
                "is_admin": bool(getattr(customer, "is_admin", False)),
                "total_bookings": tb_i,
                "paid_bookings": pb_i,
                "loyalty_display": "—",
                "total_spend": spend_f,
                "total_spend_display": _money_int_str(spend_f),
                "last_booking_date": last_dt,
                "last_booking_display": last_dt.isoformat() if last_dt else "—",
            }
        )
    return out


def _booking_sort_ts(booking: Booking) -> datetime:
    if booking.created_at:
        return booking.created_at
    return datetime.combine(booking.booking_date, datetime.min.time()).replace(tzinfo=timezone.utc)


def _admin_customer_detail_context(db: Session, customer_id: int) -> dict | None:
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        return None

    bookings = (
        db.query(Booking)
        .filter(Booking.customer_id == customer_id)
        .order_by(Booking.booking_date.desc(), Booking.id.desc())
        .all()
    )
    payments = (
        db.query(Payment)
        .filter(Payment.customer_id == customer_id)
        .order_by(Payment.id.desc())
        .all()
    )

    total_bookings = len(bookings)
    paid_bookings = sum(1 for b in bookings if str(b.payment_status or "").lower() == "paid")
    total_spend = sum(
        float(p.amount or 0)
        for p in payments
        if str(p.status or "").lower() == "paid" and math.isfinite(float(p.amount or 0))
    )
    if not math.isfinite(total_spend):
        total_spend = 0.0

    booking_rows = []
    for b in bookings:
        booking_rows.append(
            {
                "id": b.id,
                "service": b.service,
                "booking_date": b.booking_date,
                "time_slot": b.time_slot,
                "vehicle": f"{b.car_model} · {b.registration_number}",
                "payment_status": b.payment_status,
                "payment_status_key": _payment_status_key(b.payment_status),
                "estimated_display": _money_int_str(b.estimated_price),
            }
        )

    payment_rows = []
    for p in payments:
        payment_rows.append(
            {
                "id": p.id,
                "amount_display": _money_int_str(p.amount),
                "method": p.method,
                "method_label": METHOD_LABELS.get(
                    (p.method or "").strip().lower(),
                    (p.method or "—").replace("_", " ").title(),
                ),
                "status": p.status or "—",
                "status_key": _payment_status_key(p.status),
                "provider": (p.provider or "—").replace("_", " ").title() if p.provider else "—",
                "created_at_display": _format_payment_datetime(p.created_at),
            }
        )

    activity: list[dict] = []
    for b in bookings:
        activity.append(
            {
                "sort_key": _booking_sort_ts(b),
                "label": "Booking",
                "detail": (
                    f"{b.service} · {b.booking_date} ({b.time_slot})"
                    f" — payment: {b.payment_status or '—'}"
                ),
            }
        )
    for p in payments:
        activity.append(
            {
                "sort_key": p.created_at,
                "label": "Payment",
                "detail": (
                    f"R{_money_int_str(p.amount)} · "
                    f"{METHOD_LABELS.get((p.method or '').strip().lower(), p.method or '—')} · "
                    f"{p.status or '—'}"
                ),
            }
        )
    activity.sort(
        key=lambda x: x["sort_key"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    recent_activity = activity[:12]
    for a in recent_activity:
        sk = a.get("sort_key")
        if isinstance(sk, datetime):
            a["at_display"] = _format_payment_datetime(sk)
        else:
            a["at_display"] = "—"

    last_booking_date = max((b.booking_date for b in bookings), default=None)

    return {
        "detail_customer": {
            "id": customer.id,
            "full_name": customer.full_name,
            "email": customer.email,
            "phone": customer.phone,
            "is_admin": bool(getattr(customer, "is_admin", False)),
            "loyalty_display": "—",
        },
        "detail_total_bookings": total_bookings,
        "detail_paid_bookings": paid_bookings,
        "detail_total_spend": total_spend,
        "detail_total_spend_display": _money_int_str(total_spend),
        "detail_last_booking_display": last_booking_date.isoformat() if last_booking_date else "—",
        "detail_booking_rows": booking_rows,
        "detail_payment_rows": payment_rows,
        "detail_recent_activity": recent_activity,
    }


@router.get("/dashboard/customers")
def admin_customers_page(
    request: Request,
    search: str | None = Query(None),
    customer_filter: str | None = Query(None, alias="filter"),
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    segment = _normalize_customer_segment_filter(customer_filter)
    search_q = (search or "").strip()
    analytics = _customer_analytics(db)
    customer_rows = _customer_list_rows(db, search=search_q, segment_filter=segment)
    filters_active = bool(search_q) or segment != "all"

    context = _build_admin_context(
        request,
        db,
        "customers",
        customer_analytics=analytics,
        customer_rows=customer_rows,
        customers_search=search_q,
        customers_segment_filter=segment,
        customers_filters_active=filters_active,
        customers_has_any=analytics["total_customers"] > 0,
    )
    return templates.TemplateResponse(request, "admin/customers.html", context)


@router.get("/dashboard/customers/{customer_id}")
def admin_customer_detail_page(
    request: Request,
    customer_id: int,
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    detail = _admin_customer_detail_context(db, customer_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Customer not found")

    context = _build_admin_context(
        request,
        db,
        "customers",
        **detail,
    )
    return templates.TemplateResponse(request, "admin/customer_detail.html", context)


@router.get("/dashboard/vehicles")
def admin_vehicles_page(
    request: Request,
    search: str | None = Query(None),
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    search_q = (search or "").strip()
    fleet_rows, fleet_metrics = _vehicle_fleet_rows(db, search=search_q)
    context = _build_admin_context(
        request,
        db,
        "vehicles",
        vehicle_rows=fleet_rows,
        vehicle_metrics=fleet_metrics,
        vehicles_search=search_q,
        vehicles_filters_active=bool(search_q),
    )
    return templates.TemplateResponse(request, "admin/vehicles.html", context)


@router.get("/dashboard/reports")
def admin_reports_page(request: Request, db: Session = Depends(get_db)):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    context = _build_admin_context(
        request,
        db,
        "reports",
        **_reports_page_data(db),
    )
    return templates.TemplateResponse(request, "admin/reports.html", context)


@router.get("/dashboard/settings")
def admin_settings_page(
    request: Request,
    saved: str | None = None,
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    s = get_or_create_app_settings(db)
    context = _build_admin_context(
        request,
        db,
        "settings",
        settings_saved=(saved == "1"),
        settings_business_name=s.business_name,
        settings_support_email=s.support_email,
        settings_contact_phone=s.contact_phone,
        settings_whatsapp_e164=s.whatsapp_e164,
        settings_address_text=s.address_text or "",
        settings_operating_hours_text=s.operating_hours_text or "",
        settings_receipt_footer_note=s.receipt_footer_note or "",
    )
    return templates.TemplateResponse(request, "admin/settings.html", context)


@router.post("/dashboard/settings")
def admin_settings_save(
    request: Request,
    business_name: str = Form(""),
    support_email: str = Form(""),
    contact_phone: str = Form(""),
    whatsapp_e164: str = Form(""),
    address_text: str = Form(""),
    operating_hours_text: str = Form(""),
    receipt_footer_note: str = Form(""),
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    s = get_or_create_app_settings(db)
    s.business_name = (business_name or "").strip()[:200] or "WashIQ"
    s.support_email = (support_email or "").strip()[:200] or "hello@washiq.co.za"
    s.contact_phone = (contact_phone or "").strip()[:120] or "—"
    s.whatsapp_e164 = (whatsapp_e164 or "").strip().replace("+", "")[:40] or "0"
    s.address_text = (address_text or "").strip()[:4000]
    s.operating_hours_text = (operating_hours_text or "").strip()[:4000]
    s.receipt_footer_note = (receipt_footer_note or "").strip()[:2000] or "Thank you for choosing WashIQ."
    db.commit()

    return RedirectResponse(
        url="/dashboard/settings?saved=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _promotion_form_fields(
    *,
    title: str,
    description: str,
    badge_text: str,
    cta_text: str,
    cta_link: str,
    start_date: str,
    end_date: str,
    active_checked: bool,
) -> dict:
    return {
        "form_title": title,
        "form_description": description,
        "form_badge_text": badge_text,
        "form_cta_text": cta_text,
        "form_cta_link": cta_link,
        "form_start_date": start_date,
        "form_end_date": end_date,
        "form_active_checked": active_checked,
    }


def _parse_promotion_submission(
    *,
    title: str,
    description: str,
    badge_text: str,
    cta_text: str,
    cta_link: str,
    start_date: str,
    end_date: str,
    active: str | None,
) -> tuple[str | None, dict | None]:
    """
    Validate promotion form body. Returns (error_message, payload) where payload keys:
    title, description, badge_text, cta_text, cta_link, start_date, end_date, is_active (bool).
    """
    title_t = (title or "").strip()[:200]
    desc_t = (description or "").strip()
    badge_t = (badge_text or "").strip()[:80]
    cta_txt_t = (cta_text or "").strip()[:120]
    cta_lnk_t = (cta_link or "").strip()[:500]
    start_raw = (start_date or "").strip()
    end_raw = (end_date or "").strip()
    is_active = truthy_active(active)

    fields = _promotion_form_fields(
        title=title_t,
        description=desc_t,
        badge_text=badge_t,
        cta_text=cta_txt_t,
        cta_link=cta_lnk_t,
        start_date=start_raw,
        end_date=end_raw,
        active_checked=is_active,
    )

    if not title_t:
        return "Title is required.", None
    if not desc_t:
        return "Description is required.", None

    sd, err1 = parse_optional_date(start_raw, "start date")
    ed, err2 = parse_optional_date(end_raw, "end date")
    if err1 or err2:
        return err1 or err2, None

    if sd is not None and ed is not None and ed < sd:
        return "End date must be on or after the start date.", None

    return None, {
        **fields,
        "title_t": title_t,
        "desc_t": desc_t,
        "badge_t": badge_t,
        "cta_txt_t": cta_txt_t,
        "cta_lnk_t": cta_lnk_t,
        "sd": sd,
        "ed": ed,
        "is_active": is_active,
    }


def _promotions_page_context(
    request: Request,
    db: Session,
    *,
    promo_form_error: str | None = None,
    promo_created: bool = False,
    promo_updated: bool = False,
    promo_deleted: bool = False,
    form_title: str = "",
    form_description: str = "",
    form_badge_text: str = "",
    form_cta_text: str = "",
    form_cta_link: str = "",
    form_start_date: str = "",
    form_end_date: str = "",
    form_active_checked: bool = True,
) -> dict:
    return _build_admin_context(
        request,
        db,
        "promotions",
        promotions=promotion_rows_for_admin(db),
        promo_summary=promotion_admin_summary(db),
        promo_form_error=promo_form_error,
        promo_created=promo_created,
        promo_updated=promo_updated,
        promo_deleted=promo_deleted,
        form_title=form_title,
        form_description=form_description,
        form_badge_text=form_badge_text,
        form_cta_text=form_cta_text,
        form_cta_link=form_cta_link,
        form_start_date=form_start_date,
        form_end_date=form_end_date,
        form_active_checked=form_active_checked,
    )


@router.get("/dashboard/promotions")
def admin_promotions_page(
    request: Request,
    db: Session = Depends(get_db),
    created: str | None = None,
    updated: str | None = None,
    deleted: str | None = None,
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    ctx = _promotions_page_context(
        request,
        db,
        promo_created=(created == "1"),
        promo_updated=(updated == "1"),
        promo_deleted=(deleted == "1"),
    )
    return templates.TemplateResponse(request, "admin/promotions.html", ctx)


@router.post("/dashboard/promotions")
def admin_promotions_create(
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    badge_text: str = Form(""),
    cta_text: str = Form(""),
    cta_link: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    err, payload = _parse_promotion_submission(
        title=title,
        description=description,
        badge_text=badge_text,
        cta_text=cta_text,
        cta_link=cta_link,
        start_date=start_date,
        end_date=end_date,
        active=active,
    )
    if err:
        ctx = _promotions_page_context(
            request,
            db,
            promo_form_error=err,
            **_promotion_form_fields(
                title=(title or "").strip()[:200],
                description=(description or "").strip(),
                badge_text=(badge_text or "").strip()[:80],
                cta_text=(cta_text or "").strip()[:120],
                cta_link=(cta_link or "").strip()[:500],
                start_date=(start_date or "").strip(),
                end_date=(end_date or "").strip(),
                active_checked=truthy_active(active),
            ),
        )
        return templates.TemplateResponse(request, "admin/promotions.html", ctx)

    assert payload is not None
    promo = Promotion(
        title=payload["title_t"],
        description=payload["desc_t"],
        active=payload["is_active"],
        start_date=payload["sd"],
        end_date=payload["ed"],
        badge_text=payload["badge_t"] or None,
        cta_text=payload["cta_txt_t"] or None,
        cta_link=payload["cta_lnk_t"] or None,
    )
    db.add(promo)
    db.commit()

    return RedirectResponse(
        url="/dashboard/promotions?created=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _promotion_edit_context(
    request: Request,
    db: Session,
    *,
    promo: Promotion,
    promo_form_error: str | None = None,
) -> dict:
    start_s = promo.start_date.isoformat() if promo.start_date else ""
    end_s = promo.end_date.isoformat() if promo.end_date else ""
    return _build_admin_context(
        request,
        db,
        "promotions",
        edit_promotion_id=promo.id,
        promo_form_error=promo_form_error,
        form_title=promo.title or "",
        form_description=promo.description or "",
        form_badge_text=(promo.badge_text or "").strip(),
        form_cta_text=(promo.cta_text or "").strip(),
        form_cta_link=(promo.cta_link or "").strip(),
        form_start_date=start_s,
        form_end_date=end_s,
        form_active_checked=truthy_active(promo.active),
    )


@router.get("/dashboard/promotions/{promotion_id}/edit")
def admin_promotion_edit_page(
    request: Request,
    promotion_id: int,
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    promo = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promo:
        return RedirectResponse(url="/dashboard/promotions", status_code=status.HTTP_303_SEE_OTHER)

    ctx = _promotion_edit_context(request, db, promo=promo)
    return templates.TemplateResponse(request, "admin/promotion_edit.html", ctx)


@router.post("/dashboard/promotions/{promotion_id}/edit")
def admin_promotion_update(
    request: Request,
    promotion_id: int,
    title: str = Form(""),
    description: str = Form(""),
    badge_text: str = Form(""),
    cta_text: str = Form(""),
    cta_link: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    promo = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promo:
        return RedirectResponse(url="/dashboard/promotions", status_code=status.HTTP_303_SEE_OTHER)

    err, payload = _parse_promotion_submission(
        title=title,
        description=description,
        badge_text=badge_text,
        cta_text=cta_text,
        cta_link=cta_link,
        start_date=start_date,
        end_date=end_date,
        active=active,
    )
    if err:
        ctx = _promotion_edit_context(request, db, promo=promo, promo_form_error=err)
        ctx.update(
            _promotion_form_fields(
                title=(title or "").strip()[:200],
                description=(description or "").strip(),
                badge_text=(badge_text or "").strip()[:80],
                cta_text=(cta_text or "").strip()[:120],
                cta_link=(cta_link or "").strip()[:500],
                start_date=(start_date or "").strip(),
                end_date=(end_date or "").strip(),
                active_checked=truthy_active(active),
            )
        )
        return templates.TemplateResponse(request, "admin/promotion_edit.html", ctx)

    assert payload is not None
    promo.title = payload["title_t"]
    promo.description = payload["desc_t"]
    promo.active = payload["is_active"]
    promo.start_date = payload["sd"]
    promo.end_date = payload["ed"]
    promo.badge_text = payload["badge_t"] or None
    promo.cta_text = payload["cta_txt_t"] or None
    promo.cta_link = payload["cta_lnk_t"] or None
    db.commit()

    return RedirectResponse(
        url="/dashboard/promotions?updated=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/dashboard/promotions/{promotion_id}/delete")
def admin_promotion_delete(
    promotion_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    promo = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promo:
        return RedirectResponse(url="/dashboard/promotions", status_code=status.HTTP_303_SEE_OTHER)

    db.delete(promo)
    db.commit()

    return RedirectResponse(
        url="/dashboard/promotions?deleted=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/dashboard/promotions/{promotion_id}/toggle-active")
def admin_promotion_toggle_active(
    promotion_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    promo = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promo:
        return RedirectResponse(url="/dashboard/promotions", status_code=status.HTTP_303_SEE_OTHER)

    promo.active = not truthy_active(promo.active)
    db.commit()

    return RedirectResponse(url="/dashboard/promotions", status_code=status.HTTP_303_SEE_OTHER)


def _communication_log_rows(db: Session, limit: int = 80) -> list[dict]:
    rows = recent_communication_logs(db, limit=limit)
    out: list[dict] = []
    for r in rows:
        body = (r.body or "").replace("\n", " ").strip()
        out.append(
            {
                "id": r.id,
                "channel": r.channel,
                "message_type": r.message_type,
                "recipient": r.recipient,
                "subject": r.subject or "—",
                "status": r.status,
                "created_at_display": _format_payment_datetime(r.created_at),
                "body_preview": body[:160] + ("…" if len(body) > 160 else ""),
            }
        )
    return out


@router.get("/dashboard/communications")
def admin_communications_page(request: Request, db: Session = Depends(get_db)):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    context = _build_admin_context(
        request,
        db,
        "communications",
        communication_rows=_communication_log_rows(db),
        communication_metrics=_communication_metrics(db),
    )
    return templates.TemplateResponse(request, "admin/communications.html", context)


def _normalize_feedback_rating_query(raw: str | None) -> int | None:
    if raw is None or str(raw).strip() == "" or str(raw).strip().lower() == "all":
        return None
    try:
        n = int(str(raw).strip())
    except ValueError:
        return None
    return n if 1 <= n <= 5 else None


@router.get("/dashboard/feedback")
def admin_feedback_page(
    request: Request,
    rating: str | None = Query(None),
    search: str | None = Query(None),
    db: Session = Depends(get_db),
):
    redir = _admin_redirect(request, db)
    if redir:
        return redir

    rating_f = _normalize_feedback_rating_query(rating)
    search_q = (search or "").strip()
    filters_active = rating_f is not None or bool(search_q)

    context = _build_admin_context(
        request,
        db,
        "feedback",
        feedback_metrics=feedback_admin_metrics(db),
        feedback_rows=admin_feedback_rows_filtered(
            db, rating=rating_f, search=search_q, limit=500
        ),
        feedback_avg_rating=average_feedback_rating(db),
        feedback_rating_filter=rating_f if rating_f is not None else "all",
        feedback_search=search_q,
        feedback_filters_active=filters_active,
    )
    return templates.TemplateResponse(request, "admin/feedback.html", context)


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
        customer_id=booking.customer_id,
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


def _safe_bookings_redirect(return_to: str) -> str:
    allowed = {"/dashboard", "/dashboard/bookings"}
    rt = (return_to or "").strip()
    return rt if rt in allowed else "/dashboard/bookings"


@router.post("/dashboard/booking/{booking_id}/mark-collected")
def mark_booking_collected(
    booking_id: int,
    request: Request,
    return_to: str = Form("/dashboard/bookings"),
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        return RedirectResponse(
            url=_safe_bookings_redirect(return_to),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if booking.service != OVERNIGHT_SERVICE:
        return RedirectResponse(
            url=_safe_bookings_redirect(return_to),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if (booking.collection_status or "").strip().lower() == "collected":
        return RedirectResponse(
            url=_safe_bookings_redirect(return_to),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    booking.collection_status = "collected"
    booking.collected_at = datetime.now(timezone.utc)
    db.commit()

    return RedirectResponse(
        url=_safe_bookings_redirect(return_to),
        status_code=status.HTTP_303_SEE_OTHER,
    )
