import re

from pydantic import field_validator

from app.schemas.common import CamelModel

# "+<country code> <number>", as the settings phone field sends it. E.164 caps a
# full number (country code included) at 15 digits.
_PHONE_RE = re.compile(r"^\+\d{1,4}[\d\s-]*$")


class BrandingRead(CamelModel):
    logo_url: str


class BrandingUpdate(CamelModel):
    logo_url: str


class ProfileUpdate(CamelModel):
    full_name: str
    email: str
    phone: str = ""

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        digit_count = sum(ch.isdigit() for ch in value)
        if not _PHONE_RE.match(value) or not 8 <= digit_count <= 15:
            raise ValueError("Enter a valid phone number with its country code, e.g. +91 98200 11223")
        return value


class NotificationsRead(CamelModel):
    new_booking: bool
    payments: bool
    reviews: bool
    marketing: bool


class NotificationsUpdate(CamelModel):
    new_booking: bool
    payments: bool
    reviews: bool
    marketing: bool
