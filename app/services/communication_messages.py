"""Plain-text bodies for booking and payment communications."""

from __future__ import annotations

from typing import Any


def booking_email_subject(booking_id: int) -> str:
    return f"WashIQ — Booking confirmed (#{booking_id})"


def build_booking_confirmation_email(
    customer_name: str,
    booking: Any,
) -> tuple[str, str]:
    subject = booking_email_subject(booking.id)
    lines = [
        f"Hi {customer_name},",
        "",
        "Thanks for booking with WashIQ. Here are your details:",
        "",
        f"Booking ID: #{booking.id}",
        f"Service: {booking.service}",
        f"Date: {booking.booking_date}",
        f"Time: {booking.time_slot}",
        f"Vehicle: {booking.car_model}",
        f"Registration: {booking.registration_number}",
        f"Status: {booking.status}",
        f"Estimated price: R{int(booking.estimated_price or 0)}",
        "",
        "We look forward to seeing you. Use Contact on the website if you need to make changes.",
        "",
        "— The WashIQ team",
    ]
    return subject, "\n".join(lines)


def build_booking_confirmation_sms(customer_name: str, booking: Any) -> str:
    return (
        f"WashIQ: Hi {customer_name}, booking #{booking.id} confirmed — "
        f"{booking.service} on {booking.booking_date} at {booking.time_slot}. "
        f"{booking.car_model} / {booking.registration_number}. Status: {booking.status}."
    )[:480]


def payment_email_subject(payment_id: int) -> str:
    return f"WashIQ — Payment receipt (#{payment_id})"


def build_payment_confirmation_email(
    customer_name: str,
    payment: Any,
    item_label: str,
) -> tuple[str, str]:
    subject = payment_email_subject(payment.id)
    ref = payment.provider_reference or "—"
    lines = [
        f"Hi {customer_name},",
        "",
        "Your payment has been recorded. Keep this email as your digital receipt.",
        "",
        f"Payment ID: #{payment.id}",
        f"Reference: {ref}",
        f"Item: {item_label}",
        f"Amount: R{int(payment.amount or 0)}",
        f"Method: {payment.method}",
        f"Status: {payment.status}",
        f"Provider: {payment.provider}",
        "",
        "View your printable receipt online after signing in (Receipts in your account).",
        "",
        "Thank you for choosing WashIQ.",
        "",
        "— The WashIQ team",
    ]
    return subject, "\n".join(lines)


def build_payment_confirmation_sms(payment: Any, item_label: str) -> str:
    ref = payment.provider_reference or str(payment.id)
    return (
        f"WashIQ: Payment #{payment.id} recorded — {item_label}. "
        f"R{int(payment.amount or 0)} via {payment.method}. Ref: {ref}. Status: {payment.status}."
    )[:480]
