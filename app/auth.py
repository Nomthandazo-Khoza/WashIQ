from passlib.context import CryptContext
from fastapi import Request
from sqlalchemy.orm import Session

from app.models import Customer

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Must match the seeded admin in `app.main` startup (single source for email string).
ADMIN_SEED_EMAIL = "admin@washiq.local"


def coerce_is_admin(flag: object) -> bool:
    """SQLite / legacy rows sometimes store booleans as 0/1 or strings; normalize for checks."""
    if flag is True:
        return True
    if flag is None or flag is False:
        return False
    if isinstance(flag, (int, float)) and int(flag) == 1:
        return True
    if isinstance(flag, str) and flag.strip().lower() in ("1", "true", "yes", "on"):
        return True
    return bool(flag)


def _truncate_utf8_to_bytes(text: str, limit_bytes: int) -> str:
    """Truncate a string so its UTF-8 encoding is <= limit_bytes without splitting characters."""
    text = text or ""
    if limit_bytes <= 0:
        return ""

    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return text

    # Build up by character to avoid cutting a multi-byte char in half.
    total = 0
    out_chars: list[str] = []
    for ch in text:
        ch_bytes = ch.encode("utf-8")
        if total + len(ch_bytes) > limit_bytes:
            break
        out_chars.append(ch)
        total += len(ch_bytes)
    return "".join(out_chars)


def hash_password(password: str) -> str:
    # bcrypt has a max length of 72 bytes; truncate to avoid passlib errors.
    password = _truncate_utf8_to_bytes(password or "", 72)
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    plain_password = _truncate_utf8_to_bytes(plain_password or "", 72)
    return pwd_context.verify(plain_password, password_hash)


def get_current_customer(request: Request, db: Session):
    customer_id = request.session.get("customer_id")
    if not customer_id:
        return None
    return db.query(Customer).filter(Customer.id == customer_id).first()


def customer_template_view(customer: Customer) -> dict:
    """
    Plain dict for Jinja (dot-access works on dict keys).
    Avoids passing live SQLAlchemy instances into templates, which can break if the
    DB session lifecycle or middleware ordering differs between environments.
    """
    return {
        "id": customer.id,
        "full_name": customer.full_name,
        "email": customer.email,
        "phone": customer.phone,
        "is_admin": coerce_is_admin(customer.is_admin),
    }


def auth_template_context(request, db):
    try:
        customer = get_current_customer(request, db)
    except Exception:
        customer = None

    context = {
        "current_customer": customer,
        "is_authenticated": bool(customer),
        "is_admin": is_admin_customer(customer) if customer else False,
    }

    return context


def is_admin_customer(customer: Customer | None) -> bool:
    if customer is None:
        return False
    return coerce_is_admin(getattr(customer, "is_admin", None))
