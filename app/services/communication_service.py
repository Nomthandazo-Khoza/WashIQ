"""
Orchestrates email/SMS with CommunicationLog rows and safe fallbacks.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import CommunicationLog
from app.services import email_service, sms_service
from app.services.communication_messages import (
    build_booking_confirmation_email,
    build_booking_confirmation_sms,
    build_payment_confirmation_email,
    build_payment_confirmation_sms,
)
from app.services.messaging_config import load_smtp_settings

logger = logging.getLogger("washiq.messaging")

MESSAGE_BOOKING = "booking_confirmation"
MESSAGE_PAYMENT = "payment_confirmation"


def _append_log(
    db: Session,
    *,
    customer_id: int | None,
    booking_id: int | None,
    payment_id: int | None,
    channel: str,
    message_type: str,
    recipient: str,
    subject: str | None,
    body: str,
    status: str,
) -> None:
    row = CommunicationLog(
        customer_id=customer_id,
        booking_id=booking_id,
        payment_id=payment_id,
        channel=channel,
        message_type=message_type,
        recipient=recipient[:200],
        subject=(subject[:500] if subject else None),
        body=body,
        status=status,
    )
    db.add(row)
    db.commit()


def dispatch_booking_confirmation(
    db: Session,
    *,
    customer_id: int | None,
    booking: Any,
    contact_email: str,
    contact_phone: str,
    customer_name: str,
) -> list[dict[str, str]]:
    """Send/simulate booking confirmation on email + SMS; returns channel summaries for UI."""
    out: list[dict[str, str]] = []
    bid = booking.id

    # --- Email ---
    subject, email_body = build_booking_confirmation_email(customer_name, booking)
    email_to = (contact_email or "").strip()
    cfg = load_smtp_settings()
    if not email_to:
        out.append({"channel": "email", "status": "skipped", "detail": "No email address"})
    elif not cfg.enabled:
        logger.info("Booking email simulated (SMTP not configured) to=%s\n%s", email_to, email_body)
        _append_log(
            db,
            customer_id=customer_id,
            booking_id=bid,
            payment_id=None,
            channel="email",
            message_type=MESSAGE_BOOKING,
            recipient=email_to,
            subject=subject,
            body=email_body,
            status="simulated",
        )
        out.append({"channel": "email", "status": "simulated", "detail": "Logged (configure SMTP to send)"})
    else:
        ok, err = email_service.send_email(email_to, subject, email_body)
        st = "sent" if ok else "failed"
        _append_log(
            db,
            customer_id=customer_id,
            booking_id=bid,
            payment_id=None,
            channel="email",
            message_type=MESSAGE_BOOKING,
            recipient=email_to,
            subject=subject,
            body=email_body,
            status=st,
        )
        out.append(
            {
                "channel": "email",
                "status": st,
                "detail": "" if ok else (err or "send error"),
            }
        )

    # --- SMS ---
    sms_body = build_booking_confirmation_sms(customer_name, booking)
    phone = (contact_phone or "").strip()
    if not phone:
        out.append({"channel": "sms", "status": "skipped", "detail": "No phone number"})
    else:
        ok, err = sms_service.send_sms(phone, sms_body)
        if err == "not_configured":
            logger.info("Booking SMS simulated (SMS not configured) to=%s\n%s", phone, sms_body)
            _append_log(
                db,
                customer_id=customer_id,
                booking_id=bid,
                payment_id=None,
                channel="sms",
                message_type=MESSAGE_BOOKING,
                recipient=phone,
                subject=None,
                body=sms_body,
                status="simulated",
            )
            out.append({"channel": "sms", "status": "simulated", "detail": "Logged (configure SMS webhook to send)"})
        elif ok:
            _append_log(
                db,
                customer_id=customer_id,
                booking_id=bid,
                payment_id=None,
                channel="sms",
                message_type=MESSAGE_BOOKING,
                recipient=phone,
                subject=None,
                body=sms_body,
                status="sent",
            )
            out.append({"channel": "sms", "status": "sent", "detail": ""})
        else:
            _append_log(
                db,
                customer_id=customer_id,
                booking_id=bid,
                payment_id=None,
                channel="sms",
                message_type=MESSAGE_BOOKING,
                recipient=phone,
                subject=None,
                body=sms_body,
                status="failed",
            )
            out.append({"channel": "sms", "status": "failed", "detail": err or "send error"})

    return out


def dispatch_payment_confirmation(
    db: Session,
    *,
    customer_id: int | None,
    payment: Any,
    booking: Any | None,
    contact_email: str,
    contact_phone: str,
    customer_name: str,
    item_label: str,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    pid = payment.id
    bid = booking.id if booking else None

    subject, email_body = build_payment_confirmation_email(customer_name, payment, item_label)
    email_to = (contact_email or "").strip()
    cfg = load_smtp_settings()

    if not email_to:
        out.append({"channel": "email", "status": "skipped", "detail": "No email address"})
    elif not cfg.enabled:
        logger.info("Payment email simulated (SMTP not configured) to=%s\n%s", email_to, email_body)
        _append_log(
            db,
            customer_id=customer_id,
            booking_id=bid,
            payment_id=pid,
            channel="email",
            message_type=MESSAGE_PAYMENT,
            recipient=email_to,
            subject=subject,
            body=email_body,
            status="simulated",
        )
        out.append({"channel": "email", "status": "simulated", "detail": "Logged (configure SMTP to send)"})
    else:
        ok, err = email_service.send_email(email_to, subject, email_body)
        st = "sent" if ok else "failed"
        _append_log(
            db,
            customer_id=customer_id,
            booking_id=bid,
            payment_id=pid,
            channel="email",
            message_type=MESSAGE_PAYMENT,
            recipient=email_to,
            subject=subject,
            body=email_body,
            status=st,
        )
        out.append({"channel": "email", "status": st, "detail": "" if ok else (err or "send error")})

    sms_body = build_payment_confirmation_sms(payment, item_label)
    phone = (contact_phone or "").strip()
    if not phone:
        out.append({"channel": "sms", "status": "skipped", "detail": "No phone number"})
    else:
        ok, err = sms_service.send_sms(phone, sms_body)
        if err == "not_configured":
            logger.info("Payment SMS simulated (SMS not configured) to=%s\n%s", phone, sms_body)
            _append_log(
                db,
                customer_id=customer_id,
                booking_id=bid,
                payment_id=pid,
                channel="sms",
                message_type=MESSAGE_PAYMENT,
                recipient=phone,
                subject=None,
                body=sms_body,
                status="simulated",
            )
            out.append({"channel": "sms", "status": "simulated", "detail": "Logged (configure SMS webhook to send)"})
        elif ok:
            _append_log(
                db,
                customer_id=customer_id,
                booking_id=bid,
                payment_id=pid,
                channel="sms",
                message_type=MESSAGE_PAYMENT,
                recipient=phone,
                subject=None,
                body=sms_body,
                status="sent",
            )
            out.append({"channel": "sms", "status": "sent", "detail": ""})
        else:
            _append_log(
                db,
                customer_id=customer_id,
                booking_id=bid,
                payment_id=pid,
                channel="sms",
                message_type=MESSAGE_PAYMENT,
                recipient=phone,
                subject=None,
                body=sms_body,
                status="failed",
            )
            out.append({"channel": "sms", "status": "failed", "detail": err or "send error"})

    return out


def recent_communication_logs(db: Session, limit: int = 75) -> list[CommunicationLog]:
    return (
        db.query(CommunicationLog)
        .order_by(CommunicationLog.id.desc())
        .limit(limit)
        .all()
    )
