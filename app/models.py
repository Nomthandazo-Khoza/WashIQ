from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
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

    customer = relationship("Customer", back_populates="bookings")
    payments = relationship("Payment", back_populates="booking")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=True)
    method = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False, default=0)
    status = Column(String(30), nullable=False, default="pending")
    provider = Column(String(50), nullable=False, default="manual")
    provider_reference = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    booking = relationship("Booking", back_populates="payments")
