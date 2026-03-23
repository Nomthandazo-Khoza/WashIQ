from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import auth_template_context, get_current_customer, is_admin_customer
from app.database import get_db
from app.models import Booking
from app.routes.customer import attach_customer_sidebar_nav
from app.services.communication_service import dispatch_booking_confirmation

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SERVICE_PRICES = {
    "Car Wash": 80,
    "Full Valet": 180,
    "Overnight Parking": 120,
}

TIME_SLOTS = [
    "08:00",
    "09:00",
    "10:00",
    "11:00",
    "13:00",
    "14:00",
    "15:00",
    "16:00",
]


def _next_available_slot() -> str:
    current_time = datetime.now().strftime("%H:%M")
    for slot in TIME_SLOTS:
        if slot >= current_time:
            return slot
    return TIME_SLOTS[0]


def _empty_form() -> Dict[str, str]:
    return {
        "full_name": "",
        "phone": "",
        "email": "",
        "service": "",
        "booking_date": "",
        "time_slot": "",
        "registration_number": "",
        "car_model": "",
        "notes": "",
    }


def _booking_context(request: Request, **kwargs):
    base = {
        "request": request,
        "form_data": _empty_form(),
        "error_message": None,
        "field_errors": {},
        "success_booking": None,
        "booking_confirmation_channels": None,
        "service_prices": SERVICE_PRICES,
        "time_slots": TIME_SLOTS,
        "selected_price": 0,
        "next_available_slot": _next_available_slot(),
        "today_date": date.today().isoformat(),
    }
    base.update(kwargs)
    return base


@router.get("/booking")
def booking_page(
    request: Request,
    db: Session = Depends(get_db),
    service: str | None = None,
):
    current_customer = get_current_customer(request, db)
    # Customers must be logged in before booking.
    if not current_customer:
        # Preserve the booking intent (and selected service) through the login flow.
        # The login handler uses `next` to redirect back after authentication.
        service_param = None
        if service:
            normalized = service.strip()
            if normalized in SERVICE_PRICES:
                # Keep query encoding simple; FastAPI will decode it back on redirect.
                service_param = normalized

        next_url = "/booking"
        if service_param:
            next_url = f"/booking?service={service_param}"
        return RedirectResponse(
            url=f"/login?next={next_url}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if current_customer and is_admin_customer(current_customer):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    context = _booking_context(request)

    # Prefill selected service when provided (e.g. /booking?service=Car%20Wash).
    selected_service = service.strip() if service else None
    if selected_service and selected_service in SERVICE_PRICES:
        context["form_data"]["service"] = selected_service
        context["selected_price"] = SERVICE_PRICES[selected_service]

    context.update(auth_template_context(request, db))
    attach_customer_sidebar_nav(db, request, context, "booking")
    return templates.TemplateResponse(request, "booking.html", context)


@router.post("/booking")
def submit_booking(
    request: Request,
    full_name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    service: str = Form(""),
    booking_date: str = Form(""),
    time_slot: str = Form(""),
    registration_number: str = Form(""),
    car_model: str = Form(""),
    notes: Optional[str] = Form(""),
    db: Session = Depends(get_db),
):
    current_customer = get_current_customer(request, db)
    # Customers must be logged in before booking.
    if not current_customer:
        return RedirectResponse(
            url="/login?next=/booking",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if current_customer and is_admin_customer(current_customer):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    form_data = {
        "full_name": full_name.strip(),
        "phone": phone.strip(),
        "email": email.strip().lower(),
        "service": service.strip(),
        "booking_date": booking_date.strip(),
        "time_slot": time_slot.strip(),
        "registration_number": registration_number.strip().upper(),
        "car_model": car_model.strip(),
        "notes": (notes or "").strip(),
    }

    selected_price = SERVICE_PRICES.get(form_data["service"], 0)

    field_errors: Dict[str, str] = {}

    required_fields = {
        "full_name": "Full name is required.",
        "phone": "Phone number is required.",
        "email": "Email address is required.",
        "service": "Please select a service.",
        "booking_date": "Please choose a booking date.",
        "time_slot": "Please choose a time slot.",
        "registration_number": "Registration number is required.",
        "car_model": "Car model is required.",
    }
    for field_name, message in required_fields.items():
        if not form_data[field_name]:
            field_errors[field_name] = message

    if form_data["email"] and ("@" not in form_data["email"] or "." not in form_data["email"]):
        field_errors["email"] = "Please enter a valid email address."

    if form_data["service"] and form_data["service"] not in SERVICE_PRICES:
        field_errors["service"] = "Please select a valid service option."
        selected_price = 0

    if form_data["time_slot"] and form_data["time_slot"] not in TIME_SLOTS:
        field_errors["time_slot"] = "Please choose one of the available time slots."

    try:
        booking_date_value = date.fromisoformat(form_data["booking_date"])
    except ValueError:
        booking_date_value = None
        if form_data["booking_date"]:
            field_errors["booking_date"] = "Please enter a valid booking date."

    if booking_date_value and booking_date_value < date.today():
        field_errors["booking_date"] = "Booking date cannot be in the past."

    if field_errors:
        context = _booking_context(
            request,
            form_data=form_data,
            field_errors=field_errors,
            selected_price=selected_price,
            error_message="Please review the highlighted fields and try again.",
        )
        context.update(auth_template_context(request, db))
        attach_customer_sidebar_nav(db, request, context, "booking")
        return templates.TemplateResponse(request, "booking.html", context)

    current_customer = get_current_customer(request, db)

    collection_status = (
        "pending" if form_data["service"] == "Overnight Parking" else "not_applicable"
    )

    booking = Booking(
        customer_id=current_customer.id if current_customer else None,
        service=form_data["service"],
        booking_date=booking_date_value,
        time_slot=form_data["time_slot"],
        registration_number=form_data["registration_number"],
        car_model=form_data["car_model"],
        notes=form_data["notes"] or None,
        estimated_price=selected_price,
        payment_status="unpaid",
        status="pending",
        collection_status=collection_status,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    display_name = current_customer.full_name if current_customer else form_data["full_name"]

    confirmation_channels = dispatch_booking_confirmation(
        db,
        customer_id=current_customer.id if current_customer else None,
        booking=booking,
        contact_email=form_data["email"],
        contact_phone=form_data["phone"],
        customer_name=display_name,
    )

    context = _booking_context(
        request,
        success_booking={
            "id": booking.id,
            "full_name": display_name,
            "service": booking.service,
            "booking_date": booking.booking_date.isoformat(),
            "time_slot": booking.time_slot,
            "registration_number": booking.registration_number,
            "car_model": booking.car_model,
            "estimated_price": selected_price,
            "status": booking.status,
        },
        booking_confirmation_channels=confirmation_channels,
    )
    context.update(auth_template_context(request, db))
    attach_customer_sidebar_nav(db, request, context, "booking")
    return templates.TemplateResponse(request, "booking.html", context)
