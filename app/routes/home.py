from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import auth_template_context
from app.contact_info import (
    ADDRESS_LINES,
    EMAIL,
    MAP_EMBED_URL,
    OPERATING_HOURS,
    PHONE_DISPLAY,
    PHONE_E164_DIGITS,
    WHATSAPP_URL,
)
from app.database import get_db
from app.feedback_helpers import home_testimonials
from app.promotion_helpers import get_homepage_promotion

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _contact_page_context(request: Request, db: Session, **extra) -> dict:
    tel_href = f"tel:+{PHONE_E164_DIGITS.lstrip('+')}"
    ctx = {
        "request": request,
        "contact_address_lines": ADDRESS_LINES,
        "contact_phone_display": PHONE_DISPLAY,
        "contact_phone_tel": tel_href,
        "contact_email": EMAIL,
        "contact_hours": OPERATING_HOURS,
        "contact_map_embed_url": MAP_EMBED_URL,
        "contact_whatsapp_url": WHATSAPP_URL,
    }
    ctx.update(extra)
    ctx.update(auth_template_context(request, db))
    return ctx


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    tel_href = f"tel:+{PHONE_E164_DIGITS.lstrip('+')}"
    context = {
        "request": request,
        "recent_testimonials": home_testimonials(db, limit=3),
        "active_promotion": get_homepage_promotion(db),
        "home_tel_href": tel_href,
        "home_phone_display": PHONE_DISPLAY,
        "home_whatsapp_url": WHATSAPP_URL,
        "home_address_lines": ADDRESS_LINES,
        "home_hours": OPERATING_HOURS,
    }
    context.update(auth_template_context(request, db))
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/contact")
def contact_get(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "contact.html",
        _contact_page_context(
            request,
            db,
            form_name="",
            form_phone="",
            form_email="",
            form_message="",
        ),
    )


@router.post("/contact")
def contact_post(
    request: Request,
    name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    message: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Phase A: no persistence — validate and show a friendly success state.
    """
    name_t = (name or "").strip()
    phone_t = (phone or "").strip()
    email_t = (email or "").strip()
    message_t = (message or "").strip()

    if not all([name_t, phone_t, email_t, message_t]):
        return templates.TemplateResponse(
            request,
            "contact.html",
            _contact_page_context(
                request,
                db,
                form_error="Please complete all fields before sending.",
                form_name=name_t,
                form_phone=phone_t,
                form_email=email_t,
                form_message=message_t,
            ),
        )

    if "@" not in email_t or "." not in email_t:
        return templates.TemplateResponse(
            request,
            "contact.html",
            _contact_page_context(
                request,
                db,
                form_error="Please enter a valid email address.",
                form_name=name_t,
                form_phone=phone_t,
                form_email=email_t,
                form_message=message_t,
            ),
        )

    return templates.TemplateResponse(
        request,
        "contact.html",
        _contact_page_context(
            request,
            db,
            form_success=True,
            form_name="",
            form_phone="",
            form_email="",
            form_message="",
        ),
    )
