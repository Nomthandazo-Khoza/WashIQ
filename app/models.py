from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.database import Base


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(120), nullable=False)
    phone = Column(String(30), nullable=False)
    email = Column(String(120), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False)

    bookings = relationship("Booking", back_populates="customer")
    feedback_entries = relationship("Feedback", back_populates="customer")
    communication_logs = relationship("CommunicationLog", back_populates="customer")
    payments = relationship("Payment", back_populates="customer")


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    service = Column(String(80), nullable=False)
    booking_date = Column(Date, nullable=False)
    time_slot = Column(String(50), nullable=False)
    registration_number = Column(String(30), nullable=False)
    car_model = Column(String(80), nullable=False)
    notes = Column(Text, nullable=True)
    payment_status = Column(String(30), default="unpaid", nullable=False)
    status = Column(String(30), default="pending", nullable=False)
    estimated_price = Column(Float, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Overnight parking collection tracking (Phase E); other services use not_applicable.
    collection_status = Column(String(40), nullable=False, default="not_applicable")
    collected_at = Column(DateTime(timezone=True), nullable=True)

    customer = relationship("Customer", back_populates="bookings")
    payments = relationship("Payment", back_populates="booking")
    feedback_entries = relationship("Feedback", back_populates="booking")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    method = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False, default=0)
    status = Column(String(30), nullable=False, default="pending")
    provider = Column(String(50), nullable=False, default="manual")
    provider_reference = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    booking = relationship("Booking", back_populates="payments")
    customer = relationship("Customer", back_populates="payments")


class Feedback(Base):
    __tablename__ = "feedbacks"
    __table_args__ = (CheckConstraint("rating >= 1 AND rating <= 5", name="ck_feedback_rating_range"),)

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=True, index=True)
    rating = Column(Integer, nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    customer = relationship("Customer", back_populates="feedback_entries")
    booking = relationship("Booking", back_populates="feedback_entries")


class Promotion(Base):
    __tablename__ = "promotions"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    badge_text = Column(String(80), nullable=True)
    cta_text = Column(String(120), nullable=True)
    cta_link = Column(String(500), nullable=True)


class CommunicationLog(Base):
    __tablename__ = "communication_logs"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=True, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True, index=True)
    channel = Column(String(20), nullable=False)
    message_type = Column(String(80), nullable=False)
    recipient = Column(String(200), nullable=False)
    subject = Column(String(500), nullable=True)
    body = Column(Text, nullable=False)
    status = Column(String(30), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    customer = relationship("Customer", back_populates="communication_logs")


class AppSettings(Base):
    """
    Single-row operational settings (Phase 5). id should remain 1.
    Public pages may still use contact_info defaults until wired to read this table.
    """

    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    business_name = Column(String(200), nullable=False, default="WashIQ")
    support_email = Column(String(200), nullable=False, default="hello@washiq.co.za")
    contact_phone = Column(String(120), nullable=False, default="+27 (0)31 000 0000")
    whatsapp_e164 = Column(String(40), nullable=False, default="27310000000")
    address_text = Column(Text, nullable=False, default="")
    operating_hours_text = Column(Text, nullable=False, default="")
    receipt_footer_note = Column(Text, nullable=False, default="Thank you for choosing WashIQ.")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
