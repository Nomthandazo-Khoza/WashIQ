import logging
import os
import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base, engine
from app.models import AppSettings, CommunicationLog, Feedback, Promotion  # noqa: F401 — register metadata for create_all
from app.routes.auth import router as auth_router
from app.routes.booking import router as booking_router
from app.routes.customer import router as customer_router
from app.routes.dashboard import router as dashboard_router
from app.routes.home import router as home_router
from app.routes.payment import router as payment_router

app = FastAPI(title="WashIQ")
SESSION_SECRET_KEY = "washiq-dev-secret-key-change-me"

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)


def _show_error_details_in_response() -> bool:
    return os.environ.get("WASHIQ_DEBUG", "").lower() in ("1", "true", "yes") or (
        SESSION_SECRET_KEY == "washiq-dev-secret-key-change-me"
    )


@app.exception_handler(Exception)
async def washiq_unhandled_exception(request: Request, exc: Exception):
    """
    Unhandled errors become this handler (FastAPI wires it to ServerErrorMiddleware).
    In dev, return the traceback as plain text so the browser shows the real cause.
    """
    if isinstance(exc, StarletteHTTPException):
        return await http_exception_handler(request, exc)

    logging.getLogger("washiq").exception(
        "Unhandled error on %s %s", request.method, request.url.path
    )
    if _show_error_details_in_response():
        return PlainTextResponse(
            "WashIQ error (dev details — remove or change SESSION_SECRET_KEY in production):\n\n"
            + traceback.format_exc(),
            status_code=500,
        )
    return PlainTextResponse("Internal Server Error", status_code=500)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(home_router)
app.include_router(booking_router)
app.include_router(auth_router)
app.include_router(customer_router)
app.include_router(payment_router)
app.include_router(dashboard_router)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    # Lightweight SQLite compatibility update for existing Phase 0-4 databases.
    with engine.begin() as connection:
        customer_columns = connection.execute(text("PRAGMA table_info(customers)")).fetchall()
        customer_column_names = {column[1] for column in customer_columns}
        if "is_admin" not in customer_column_names:
            connection.execute(text("ALTER TABLE customers ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))

        # Older WashIQ DBs may predate `bookings.created_at`; admin queries order by it.
        booking_columns = connection.execute(text("PRAGMA table_info(bookings)")).fetchall()
        if booking_columns:
            booking_column_names = {column[1] for column in booking_columns}
            booking_patches: list[tuple[str, str]] = [
                (
                    "created_at",
                    "ALTER TABLE bookings ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
                ),
                (
                    "payment_status",
                    "ALTER TABLE bookings ADD COLUMN payment_status VARCHAR(30) DEFAULT 'unpaid'",
                ),
                (
                    "status",
                    "ALTER TABLE bookings ADD COLUMN status VARCHAR(30) DEFAULT 'pending'",
                ),
                (
                    "estimated_price",
                    "ALTER TABLE bookings ADD COLUMN estimated_price FLOAT DEFAULT 0",
                ),
                (
                    "collection_status",
                    "ALTER TABLE bookings ADD COLUMN collection_status VARCHAR(40) NOT NULL DEFAULT 'not_applicable'",
                ),
                (
                    "collected_at",
                    "ALTER TABLE bookings ADD COLUMN collected_at DATETIME",
                ),
            ]
            for col_name, ddl in booking_patches:
                if col_name not in booking_column_names:
                    connection.execute(text(ddl))
                    booking_column_names.add(col_name)

        payment_columns = connection.execute(text("PRAGMA table_info(payments)")).fetchall()
        if payment_columns:
            payment_column_names = {column[1] for column in payment_columns}
            if "customer_id" not in payment_column_names:
                connection.execute(
                    text("ALTER TABLE payments ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
                )

    # Hard-coded admin seed for development/testing.
    # This makes admin login work immediately without manually updating SQLite.
    from app.auth import ADMIN_SEED_EMAIL, hash_password
    from app.database import SessionLocal
    from app.models import Customer

    ADMIN_EMAIL = ADMIN_SEED_EMAIL
    ADMIN_PASSWORD = "Admin12345"
    ADMIN_FULL_NAME = "WashIQ Admin"
    ADMIN_PHONE = "000000000"

    db = SessionLocal()
    try:
        admin = db.query(Customer).filter(Customer.email == ADMIN_SEED_EMAIL).first()
        if admin:
            admin.full_name = ADMIN_FULL_NAME
            admin.phone = ADMIN_PHONE
            admin.password_hash = hash_password(ADMIN_PASSWORD)
            admin.is_admin = True
        else:
            admin = Customer(
                full_name=ADMIN_FULL_NAME,
                phone=ADMIN_PHONE,
                email=ADMIN_EMAIL,
                password_hash=hash_password(ADMIN_PASSWORD),
                is_admin=True,
            )
            db.add(admin)
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        from app.settings_helpers import get_or_create_app_settings

        get_or_create_app_settings(db)
    finally:
        db.close()
