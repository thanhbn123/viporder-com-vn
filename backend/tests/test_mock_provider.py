"""Mock provider behaviour matrix.

``MOCK_PROVIDER_BEHAVIOUR`` is the coarse switch; the phone-suffix convention is
the per-request switch. Both are asserted here so the documented behaviour in
``app/providers/mock.py`` cannot drift away from the code.
"""

from __future__ import annotations

import pytest

from app.providers.base import (
    ProviderStatus,
    RegistrationProvider,
    RegistrationRequest,
)
from app.providers.mock import SUFFIX_BEHAVIOURS, MockRegistrationProvider

REQUEST = RegistrationRequest(
    full_name="Nguyễn Văn A",
    phone="+84912345678",
    password="secret-at-least-8",
    email="a@example.com",
)


def _request_with_phone(phone: str) -> RegistrationRequest:
    return RegistrationRequest(
        full_name="Nguyễn Văn A",
        phone=phone,
        password="secret-at-least-8",
    )


def test_implements_the_provider_protocol() -> None:
    assert isinstance(MockRegistrationProvider(), RegistrationProvider)
    assert MockRegistrationProvider().name == "mock"


@pytest.mark.parametrize(
    ("behaviour", "status", "retryable"),
    [
        ("success", ProviderStatus.SUCCESS, False),
        ("duplicate", ProviderStatus.DUPLICATE, False),
        ("invalid", ProviderStatus.INVALID, False),
        ("unavailable", ProviderStatus.UNAVAILABLE, True),
        ("timeout", ProviderStatus.UNAVAILABLE, True),
    ],
)
def test_environment_selected_behaviour(
    behaviour: str, status: ProviderStatus, retryable: bool
) -> None:
    provider = MockRegistrationProvider(behaviour)
    result = provider.register(REQUEST)
    assert result.status is status
    assert result.retryable is retryable


def test_error_behaviour_raises() -> None:
    provider = MockRegistrationProvider("error")
    with pytest.raises(RuntimeError):
        provider.register(REQUEST)


def test_unknown_behaviour_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="MOCK_PROVIDER_BEHAVIOUR"):
        MockRegistrationProvider("explode")


def test_success_generates_sequential_codes() -> None:
    provider = MockRegistrationProvider()
    first = provider.register(REQUEST)
    second = provider.register(REQUEST)

    assert first.external_customer_code == "TT00001"
    assert second.external_customer_code == "TT00002"
    assert first.external_customer_id == "mock-customer-00001"


@pytest.mark.parametrize(
    ("phone", "expected_status"),
    [
        ("+84912000000", ProviderStatus.DUPLICATE),
        ("+84912000001", ProviderStatus.INVALID),
        ("+84912000002", ProviderStatus.UNAVAILABLE),
        ("+84912000003", ProviderStatus.UNAVAILABLE),
        ("+84912000005", ProviderStatus.SUCCESS),
        ("+84912001234", ProviderStatus.SUCCESS),
    ],
)
def test_phone_suffix_convention(phone: str, expected_status: ProviderStatus) -> None:
    provider = MockRegistrationProvider("success")
    result = provider.register(_request_with_phone(phone))
    assert result.status is expected_status


def test_phone_suffix_overrides_the_environment_behaviour() -> None:
    provider = MockRegistrationProvider("success")
    assert provider.behaviour_for("+84912000000") == "duplicate"
    assert provider.behaviour_for("+84912001234") == "success"


def test_documented_suffix_table_matches_the_code() -> None:
    assert SUFFIX_BEHAVIOURS == {
        "0000": "duplicate",
        "0001": "invalid",
        "0002": "unavailable",
        "0003": "timeout",
        "0004": "error",
    }


def test_missing_password_is_rejected() -> None:
    with pytest.raises(ValueError):
        RegistrationRequest(full_name="A", phone="+84912345678", password="")


def test_request_repr_hides_the_password() -> None:
    assert "secret-at-least-8" not in repr(REQUEST)
    assert "secret-at-least-8" not in str(REQUEST)


def test_redact_request_never_includes_the_password() -> None:
    from app.providers.base import redact_request

    redacted = redact_request(REQUEST)
    assert "secret-at-least-8" not in str(redacted)
    assert redacted["password"] == "<redacted>"


def test_unavailable_behaviours_carry_a_distinct_error_code() -> None:
    assert MockRegistrationProvider("timeout").register(REQUEST).error_code == "PROVIDER_TIMEOUT"
    assert (
        MockRegistrationProvider("unavailable").register(REQUEST).error_code
        == "PROVIDER_UNAVAILABLE"
    )
