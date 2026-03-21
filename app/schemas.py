from datetime import date
from typing import Optional

from pydantic import BaseModel, EmailStr


class BookingCreate(BaseModel):
    full_name: str
    phone: str
    email: EmailStr
    service: str
    booking_date: date
    time_slot: str
    registration_number: str
    car_model: str
    notes: Optional[str] = None
