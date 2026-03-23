"""
WashIQ public contact details (Phase A).
Update these values when the real business address and numbers are finalized.
"""

from urllib.parse import quote

# Display & structured data for templates / routes
ADDRESS_LINES = [
    "123 Marine Parade",
    "North Beach, Durban",
    "4001, KwaZulu-Natal",
    "South Africa",
]

PHONE_DISPLAY = "+27 (0)31 000 0000"
# E.164 without + for tel: and wa.me links (placeholder — replace with real cell)
PHONE_E164_DIGITS = "27310000000"

EMAIL = "hello@washiq.co.za"

OPERATING_HOURS = [
    {"label": "Monday – Friday", "value": "7:00 AM – 7:00 PM"},
    {"label": "Saturday", "value": "8:00 AM – 5:00 PM"},
    {"label": "Sunday", "value": "Closed"},
]

# Google Maps embed (Durban city centre — replace embed when exact address is known)
MAP_EMBED_URL = (
    "https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d110985.19726144699!2d31.0033688!3d-29.8688079!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x1ef7aa39807d50ab%3A0xbcb35431f3b80b16!2sDurban%2C%20South%20Africa!5e0!3m2!1sen!2sza!4v1709120400000!5m2!1sen!2sza"
)

WHATSAPP_URL = (
    f"https://wa.me/{PHONE_E164_DIGITS}"
    f"?text={quote('Hi WashIQ — I have a question about your services.')}"
)
