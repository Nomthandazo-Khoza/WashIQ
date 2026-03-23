from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.auth import auth_template_context, get_current_customer, is_admin_customer
from app.database import get_db
from app.models import Booking, Payment
from app.routes.customer import customer_payment_nav_href
from app.services.communication_service import dispatch_payment_confirmation
from app.settings_helpers import receipt_footer_from_settings

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

PAYMENT_TIMINGS = ["pay_now", "pay_later"]

# Canonical method values stored in `payments.method` (and submitted from the form).
PAY_NOW_METHODS = ["card", "eft", "snapscan", "zapper"]
PAY_LATER_METHODS = ["cash", "pay_at_counter"]

TIMING_LABELS: dict[str, str] = {
    "pay_now": "Pay Now",
    "pay_later": "Pay Later",
}

METHOD_LABELS: dict[str, str] = {
    "card": "Card",
    "eft": "EFT",
    "snapscan": "SnapScan",
    "zapper": "Zapper",
    "cash": "Cash",
    "pay_at_counter": "Pay at Counter",
}

PAYMENT_STATUSES: dict[str, str] = {
    "pay_now": "paid",
    "pay_later": "pending",
}

BOOKING_PAYMENT_STATUSES: dict[str, str] = {
    "pay_now": "paid",
    "pay_later": "unpaid",
}

PAYMENT_METHOD_HELP: dict[str, str] = {
    "card": "Mock card payment fields for MVP testing.",
    "eft": "Direct bank transfer (MVP simulation).",
    "snapscan": "Wallet-style payment (MVP simulation).",
    "zapper": "Wallet-style payment (MVP simulation).",
    "cash": "Pay the cashier in cash later.",
    "pay_at_counter": "Pay at the wash counter when you arrive.",
}

PACKAGES = [
    {"name": "Single Wash", "amount": 80},
    {"name": "5 Washes Package", "amount": 400},
    {"name": "Monthly Parking Plan", "amount": 1200},
]

DEFAULT_CARD_DETAILS = {
    "cardholder_name": "",
    "card_number": "",
    "expiry_date": "",
    "cvv": "",
}


def _payment_context(request: Request, db: Session, **kwargs):
    base = {
        "request": request,
        "booking": None,
        "payment_timings": PAYMENT_TIMINGS,
        "pay_now_methods": PAY_NOW_METHODS,
        "pay_later_methods": PAY_LATER_METHODS,
        "timing_labels": TIMING_LABELS,
        "method_labels": METHOD_LABELS,
        "method_help": PAYMENT_METHOD_HELP,
        "packages": PACKAGES,
        "selected_timing": "pay_now",
        "selected_method": "card",
        "selected_timing_label": TIMING_LABELS["pay_now"],
        "selected_method_label": METHOD_LABELS["card"],
        "predicted_payment_status": PAYMENT_STATUSES["pay_now"],
        "payment_method_help": PAYMENT_METHOD_HELP.get("card"),
        "selected_package": PACKAGES[0]["name"],
        "selected_amount": PACKAGES[0]["amount"],
        "status_badge": PAYMENT_STATUSES["pay_now"],
        "error_message": None,
        "payment_result": None,
        "payment_confirmation_channels": None,
        **DEFAULT_CARD_DETAILS,
    }
    base.update(auth_template_context(request, db))
    base.update(kwargs)
    cur = get_current_customer(request, db)
    if cur and not is_admin_customer(cur):
        base.setdefault("customer_section", "payment")
        base["payment_nav_href"] = customer_payment_nav_href(db, cur.id)
    return base


def _package_amount(package_name: str) -> float:
    for package in PACKAGES:
        if package["name"] == package_name:
            return float(package["amount"])
    return float(PACKAGES[0]["amount"])


def _get_predicted_status(payment_action: str) -> str:
    return PAYMENT_STATUSES.get(payment_action, PAYMENT_STATUSES["pay_now"])


def _allowed_methods_for_timing(payment_action: str) -> list[str]:
    return PAY_NOW_METHODS if payment_action == "pay_now" else PAY_LATER_METHODS


@router.get("/payment")
def payment_page(
    request: Request,
    booking_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    current_customer = get_current_customer(request, db)
    if not current_customer:
        # Keep the payment flow after login.
        next_url = "/payment"
        if booking_id is not None:
            next_url += f"?booking_id={booking_id}"
        from urllib.parse import quote

        return RedirectResponse(
            url=f"/login?next={quote(next_url, safe='/?&=')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if is_admin_customer(current_customer):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    booking = None
    if booking_id:
        booking = db.query(Booking).filter(Booking.id == booking_id).first()
        if booking:
            if booking.customer_id and booking.customer_id != current_customer.id:
                booking = None

    selected_amount = booking.estimated_price if booking else PACKAGES[0]["amount"]
    # Summary predicts the outcome of the selected timing (not the current booking/payment state).
    selected_timing = "pay_now"
    selected_method = "card"
    predicted_status = _get_predicted_status(selected_timing)

    return templates.TemplateResponse(
        request,
        "payment.html",
        _payment_context(
            request,
            db,
            booking=booking,
            selected_amount=selected_amount,
            selected_timing=selected_timing,
            selected_method=selected_method,
            selected_timing_label=TIMING_LABELS[selected_timing],
            selected_method_label=METHOD_LABELS[selected_method],
            predicted_payment_status=predicted_status,
            status_badge=predicted_status,
            payment_method_help=PAYMENT_METHOD_HELP.get(selected_method),
        ),
    )


@router.post("/payment")
def process_payment(
    request: Request,
    payment_method: str = Form("card"),
    payment_action: str = Form("pay_now"),
    selected_package: str = Form("Single Wash"),
    booking_id: str = Form(""),
    cardholder_name: str = Form(""),
    card_number: str = Form(""),
    expiry_date: str = Form(""),
    cvv: str = Form(""),
    db: Session = Depends(get_db),
):
    current_customer = get_current_customer(request, db)
    if not current_customer:
        next_url = "/payment"
        if booking_id:
            next_url += f"?booking_id={booking_id}"
        from urllib.parse import quote

        return RedirectResponse(
            url=f"/login?next={quote(next_url, safe='/?&=')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if is_admin_customer(current_customer):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    # Normalize timing and method to canonical values.
    payment_action = payment_action if payment_action in PAYMENT_TIMINGS else "pay_now"
    allowed_methods = _allowed_methods_for_timing(payment_action)
    if payment_method not in allowed_methods:
        payment_method = allowed_methods[0]

    booking = None
    booking_id_int: int | None = None
    if booking_id:
        try:
            booking_id_int = int(booking_id)
            booking = db.query(Booking).filter(Booking.id == booking_id_int).first()
        except ValueError:
            booking = None

    # If booking was requested, ensure it belongs to the logged-in customer.
    if booking and booking.customer_id and booking.customer_id != current_customer.id:
        booking = None

    if selected_package not in [package["name"] for package in PACKAGES]:
        selected_package = PACKAGES[0]["name"]

    if booking:
        amount = float(booking.estimated_price)
        item_name = f"Booking #{booking.id} - {booking.service}"
    else:
        amount = _package_amount(selected_package)
        item_name = selected_package

    # MVP validation: card details required only for Pay Now + Card.
    if payment_action == "pay_now" and payment_method == "card":
        missing = []
        if not (cardholder_name or "").strip():
            missing.append("Cardholder Name")
        if not (card_number or "").strip():
            missing.append("Card Number")
        if not (expiry_date or "").strip():
            missing.append("Expiry Date")
        if not (cvv or "").strip():
            missing.append("CVV")

        if missing:
            predicted_status = _get_predicted_status(payment_action)
            return templates.TemplateResponse(
                request,
                "payment.html",
                _payment_context(
                    request,
                    db,
                    booking=booking,
                    selected_package=selected_package,
                    selected_amount=amount,
                    selected_timing=payment_action,
                    selected_method=payment_method,
                    selected_timing_label=TIMING_LABELS[payment_action],
                    selected_method_label=METHOD_LABELS[payment_method],
                    predicted_payment_status=predicted_status,
                    status_badge=predicted_status,
                    payment_method_help=PAYMENT_METHOD_HELP.get(payment_method),
                    error_message=f"Please complete the required card fields: {', '.join(missing)}.",
                    cardholder_name=(cardholder_name or "").strip(),
                    card_number=(card_number or "").strip(),
                    expiry_date=(expiry_date or "").strip(),
                    cvv="",  # Do not echo CVV back.
                ),
            )

    payment_status = PAYMENT_STATUSES[payment_action]
    booking_payment_status = BOOKING_PAYMENT_STATUSES[payment_action]
    provider_reference = (
        f"MANUAL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{payment_method.upper()}"
        if payment_action == "pay_now"
        else None
    )

    payment = Payment(
        booking_id=booking.id if booking else None,
        customer_id=current_customer.id,
        method=payment_method,
        amount=amount,
        status=payment_status,
        provider="manual",
        provider_reference=provider_reference,
    )
    db.add(payment)

    if booking:
        booking.payment_status = booking_payment_status

    db.commit()
    db.refresh(payment)

    confirmation_channels = dispatch_payment_confirmation(
        db,
        customer_id=current_customer.id,
        payment=payment,
        booking=booking,
        contact_email=current_customer.email,
        contact_phone=current_customer.phone,
        customer_name=current_customer.full_name,
        item_label=item_name,
    )

    return templates.TemplateResponse(
        request,
        "payment.html",
        _payment_context(
            request,
            db,
            booking=booking,
            selected_timing=payment_action,
            selected_method=payment_method,
            selected_timing_label=TIMING_LABELS[payment_action],
            selected_method_label=METHOD_LABELS[payment_method],
            predicted_payment_status=payment_status,
            status_badge=payment_status,
            payment_method_help=PAYMENT_METHOD_HELP.get(payment_method),
            selected_package=selected_package,
            selected_amount=amount,
            payment_confirmation_channels=confirmation_channels,
            payment_result={
                "payment_id": payment.id,
                "item_name": item_name,
                "amount": amount,
                "timing": payment_action,
                "timing_label": TIMING_LABELS[payment_action],
                "method": payment_method,
                "method_label": METHOD_LABELS[payment_method],
                "status": payment.status,
                "provider": payment.provider,
                "provider_reference": payment.provider_reference or "Pending confirmation",
                "receipt_path": f"/receipt/{payment.id}",
            },
        ),
    )


@router.get("/receipt/{payment_id}")
def receipt_page(
    payment_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    current = get_current_customer(request, db)
    if not current:
        return RedirectResponse(
            url=f"/login?next=/receipt/{payment_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    payment = (
        db.query(Payment)
        .options(
            joinedload(Payment.booking).joinedload(Booking.customer),
            joinedload(Payment.customer),
        )
        .filter(Payment.id == payment_id)
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Receipt not found")

    admin = is_admin_customer(current)
    allowed = admin
    if not allowed:
        if payment.customer_id and payment.customer_id == current.id:
            allowed = True
        elif payment.booking_id and payment.booking and payment.booking.customer_id == current.id:
            allowed = True
    if not allowed:
        raise HTTPException(status_code=403, detail="You cannot view this receipt")

    booking = payment.booking
    cust_name = current.full_name
    if booking and booking.customer:
        cust_name = booking.customer.full_name
    elif payment.customer:
        cust_name = payment.customer.full_name

    method_label = METHOD_LABELS.get(payment.method, payment.method)

    context = {
        "request": request,
        "receipt_payment": payment,
        "receipt_booking": booking,
        "customer_display_name": cust_name,
        "method_label": method_label,
        "amount_display": int(round(float(payment.amount or 0))),
        "receipt_footer_note": receipt_footer_from_settings(db),
    }
    context.update(auth_template_context(request, db))
    return templates.TemplateResponse(request, "receipt.html", context)
