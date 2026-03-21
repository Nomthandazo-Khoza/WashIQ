from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import (
    ADMIN_SEED_EMAIL,
    auth_template_context,
    coerce_is_admin,
    get_current_customer,
    hash_password,
    is_admin_customer,
    verify_password,
)
from app.database import get_db
from app.models import Booking, Customer

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _signup_context(request: Request, db: Session, **kwargs):
    base = {
        "request": request,
        "error_message": None,
        "form_data": {"full_name": "", "phone": "", "email": ""},
    }
    base.update(auth_template_context(request, db))
    base.update(kwargs)
    return base


def _login_context(request: Request, db: Session, **kwargs):
    base = {
        "request": request,
        "error_message": None,
        "form_data": {"email": ""},
    }
    base.update(auth_template_context(request, db))
    base.update(kwargs)
    return base


@router.get("/signup")
def signup_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("signup.html", _signup_context(request, db))


@router.post("/signup")
def signup(
    request: Request,
    full_name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    db: Session = Depends(get_db),
):
    # Keep this MVP-friendly: never hard-crash signup with a 500.
    try:
        form_data = {
            "full_name": full_name.strip(),
            "phone": phone.strip(),
            "email": email.strip().lower(),
        }

        if not all(
            [
                form_data["full_name"],
                form_data["phone"],
                form_data["email"],
                password.strip(),
                confirm_password.strip(),
            ]
        ):
            return templates.TemplateResponse(
                "signup.html",
                _signup_context(
                    request,
                    db,
                    form_data=form_data,
                    error_message="All fields are required.",
                ),
            )

        if "@" not in form_data["email"] or "." not in form_data["email"]:
            return templates.TemplateResponse(
                "signup.html",
                _signup_context(
                    request,
                    db,
                    form_data=form_data,
                    error_message="Please enter a valid email address.",
                ),
            )

        if password != confirm_password:
            return templates.TemplateResponse(
                "signup.html",
                _signup_context(
                    request,
                    db,
                    form_data=form_data,
                    error_message="Password and confirm password must match.",
                ),
            )

        existing_customer = (
            db.query(Customer).filter(Customer.email == form_data["email"]).first()
        )
        if existing_customer:
            return templates.TemplateResponse(
                "signup.html",
                _signup_context(
                    request,
                    db,
                    form_data=form_data,
                    error_message="An account with this email already exists. Please log in.",
                ),
            )

        password_hash = hash_password(password)
        customer = Customer(
            full_name=form_data["full_name"],
            phone=form_data["phone"],
            email=form_data["email"],
            password_hash=password_hash,
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)

        request.session["customer_id"] = customer.id
        return RedirectResponse(
            url="/profile",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception:
        try:
            return templates.TemplateResponse(
                "signup.html",
                _signup_context(
                    request,
                    db,
                    form_data={
                        "full_name": full_name.strip(),
                        "phone": phone.strip(),
                        "email": email.strip().lower(),
                    },
                    error_message="We couldn't create your account right now. Please try again.",
                ),
            )
        except Exception:
            # Ultimate MVP fallback: never surface a hard 500 for signup.
            return PlainTextResponse(
                "We couldn't create your account right now. Please try again.",
                status_code=200,
            )


@router.get("/login")
def login_page(request: Request, next: str | None = None, db: Session = Depends(get_db)):
    # Preserve intended destination across the login form.
    next_path = next or None
    if next_path and not next_path.startswith("/"):
        next_path = None
    return templates.TemplateResponse(
        "login.html",
        _login_context(request, db, next_path=next_path),
    )


@router.post("/login")
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str | None = Form(None),
    db: Session = Depends(get_db),
):
    # Never surface raw 500s for login (dependency errors or template issues can happen).
    try:
        form_data = {"email": (email or "").strip().lower()}
        customer = db.query(Customer).filter(Customer.email == form_data["email"]).first()

        try:
            password_ok = bool(
                customer
                and customer.password_hash
                and verify_password(password, customer.password_hash)
            )
        except Exception:
            password_ok = False

        # Support both:
        # - normal browser flow: `next` comes from the hidden form input
        # - direct POSTs: `next` comes from the query string
        next_from_query = request.query_params.get("next")
        effective_next = next or next_from_query

        if not password_ok:
            next_path = effective_next or None
            if next_path and not next_path.startswith("/"):
                next_path = None
            return templates.TemplateResponse(
                "login.html",
                _login_context(
                    request,
                    db,
                    form_data=form_data,
                    error_message="Invalid email or password.",
                    next_path=next_path,
                ),
            )

        # If the DB row predates `is_admin` fixes, the seeded admin email may still be non-admin.
        # After a correct password login, ensure that account is promoted (dev seed email only).
        email_norm = (customer.email or "").strip().lower()
        if email_norm == ADMIN_SEED_EMAIL and not coerce_is_admin(customer.is_admin):
            customer.is_admin = True
            db.commit()
        db.refresh(customer)

        request.session["customer_id"] = customer.id
        next_path = effective_next or None
        if next_path and not next_path.startswith("/"):
            next_path = None

        # Admins go to the ops dashboard by default; customers fall back to profile.
        if is_admin_customer(customer):
            redirect_url = next_path or "/dashboard"
        else:
            redirect_url = next_path or "/profile"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    except Exception:
        # Best-effort fallback: render the login page with a generic error.
        try:
            form_data = {"email": (email or "").strip().lower()}
            next_from_query = request.query_params.get("next")
            effective_next = next or next_from_query
            next_path = effective_next or None
            if next_path and not next_path.startswith("/"):
                next_path = None
            return templates.TemplateResponse(
                "login.html",
                _login_context(
                    request,
                    db,
                    form_data=form_data,
                    error_message="We couldn't log you in right now. Please try again.",
                    next_path=next_path,
                ),
            )
        except Exception:
            return PlainTextResponse(
                "We couldn't log you in right now. Please try again.",
                status_code=200,
            )


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/profile")
def profile_page(request: Request, db: Session = Depends(get_db)):
    current_customer = get_current_customer(request, db)
    if not current_customer:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if current_customer and is_admin_customer(current_customer):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    bookings = (
        db.query(Booking)
        .filter(Booking.customer_id == current_customer.id)
        .order_by(Booking.created_at.desc())
        .all()
    )

    context = {
        "request": request,
        "bookings": bookings,
    }
    context.update(auth_template_context(request, db))
    return templates.TemplateResponse("profile.html", context)
