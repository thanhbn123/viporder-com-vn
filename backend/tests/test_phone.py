"""Phone normalisation."""

from __future__ import annotations

import pytest

from app.phone import InvalidPhoneError, normalise_phone, phone_display


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0912345678", "+84912345678"),
        ("+84912345678", "+84912345678"),
        ("84912345678", "+84912345678"),
        ("0912 345 678", "+84912345678"),
        ("0912.345.678", "+84912345678"),
        ("(091) 234-5678", "+84912345678"),
        ("+84 912 345 678", "+84912345678"),
        ("0084912345678", "+84912345678"),
        ("  0912345678  ", "+84912345678"),
        ("0912-345-678", "+84912345678"),
        # A second, different number, so the test is not a single-value tautology.
        ("0987 654 321", "+84987654321"),
    ],
)
def test_normalises_to_canonical_form(raw: str, expected: str) -> None:
    assert normalise_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "abc",
        "091234567",  # too short
        "09123456789",  # too long for a trunk-prefixed number
        "+8491234567",  # 8-digit national number
        "0912345a78",
        "0012345678",
        "+1 415 555 0100",  # not Vietnamese
        "0912345678x",
        "++84912345678",
    ],
)
def test_invalid_input_is_rejected(raw: str) -> None:
    with pytest.raises(InvalidPhoneError):
        normalise_phone(raw)


def test_none_is_rejected() -> None:
    with pytest.raises(InvalidPhoneError):
        normalise_phone(None)  # type: ignore[arg-type]


def test_display_form_is_readable() -> None:
    assert phone_display("+84912345678") == "0912 345 678"
