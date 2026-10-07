"""Vietnamese phone normalisation.

One canonical form is stored (``+84XXXXXXXXX``) plus a human-readable display
form. Normalising at the boundary means duplicate detection actually works:
``0912 345 678`` and ``+84912345678`` are the same customer, and a duplicate
check that compares raw strings would happily create two accounts for them.
"""

from __future__ import annotations

import re

# Characters people legitimately type as visual separators.
_SEPARATORS = re.compile(r"[\s.\-()\u00a0\u2013\u2014/]")
_COUNTRY_CODE = "84"
_NATIONAL_LENGTH = 9

# Vietnamese numbering plan: after the trunk 0 / country code, the national
# significant number is 9 digits and starts with 2 (landline) or 3/5/7/8/9
# (mobile). Rejecting a leading 0/1 catches the common "00 84..." and
# "84 0..." mistakes instead of silently storing a phone nobody can dial.
_VALID_FIRST_DIGITS = frozenset("235789")


class InvalidPhoneError(ValueError):
    """Raised when a phone number cannot be reduced to a canonical form."""


def normalise_phone(raw: str) -> str:
    """Return the canonical ``+84XXXXXXXXX`` form, or raise InvalidPhoneError."""
    if raw is None:
        raise InvalidPhoneError("phone is required")

    value = str(raw).strip()
    if not value:
        raise InvalidPhoneError("phone is required")

    value = _SEPARATORS.sub("", value)

    if value.startswith("+"):
        value = value[1:]
    elif value.startswith("00"):
        value = value[2:]

    if not value.isdigit():
        raise InvalidPhoneError("phone must contain digits only (plus separators)")

    if value.startswith(_COUNTRY_CODE) and len(value) == len(_COUNTRY_CODE) + _NATIONAL_LENGTH:
        national = value[len(_COUNTRY_CODE) :]
    elif value.startswith("0") and len(value) == 1 + _NATIONAL_LENGTH:
        national = value[1:]
    elif len(value) == _NATIONAL_LENGTH:
        national = value
    else:
        raise InvalidPhoneError(
            "phone must be a Vietnamese number of 9 digits after the trunk prefix"
        )

    if len(national) != _NATIONAL_LENGTH or not national.isdigit():
        raise InvalidPhoneError(
            "phone must be a Vietnamese number of 9 digits after the trunk prefix"
        )

    if national[0] not in _VALID_FIRST_DIGITS:
        raise InvalidPhoneError("phone is not a valid Vietnamese number")

    return f"+{_COUNTRY_CODE}{national}"


def national_number(canonical: str) -> str:
    """``+84912345678`` -> ``912345678``."""
    return canonical[len(_COUNTRY_CODE) + 1 :]


def phone_display(canonical: str) -> str:
    """``+84912345678`` -> ``0912 345 678`` (what the customer typed, roughly)."""
    national = national_number(canonical)
    if len(national) != _NATIONAL_LENGTH:
        return canonical
    return f"0{national[:3]} {national[3:6]} {national[6:]}"
