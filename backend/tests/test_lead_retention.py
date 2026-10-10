"""A lead is NEVER lost because the provider was unavailable.

This is the single most important property of G02. Every failure mode the
provider can present — a clean UNAVAILABLE result, a timeout, a raised
exception, a 500 — must leave the lead row on disk with a truthful status and
must answer the client with something better than a 5xx.
"""

from __future__ import annotations

import pytest

from app.providers.base import (
    ProviderStatus,
    RegistrationProvider,
    RegistrationResult,
)
from tests.conftest import Harness, payload


class RaisingProvider:
    """A provider that blows up, as a third-party library eventually will."""

    name = "raising"

    def __init__(self, message: str = "boom") -> None:
        self.message = message
        self.calls = 0

    def register(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise RuntimeError(self.message)


class UnavailableProvider:
    name = "unavailable"

    def __init__(self, *, retryable: bool = True, http_status: int | None = 503) -> None:
        self.retryable = retryable
        self.http_status = http_status

    def register(self, request):  # type: ignore[no-untyped-def]
        return RegistrationResult(
            status=ProviderStatus.UNAVAILABLE,
            message="provider down",
            http_status=self.http_status,
            retryable=self.retryable,
        )


class RecordingProvider:
    """Succeeds, and remembers what it was asked to do."""

    name = "recording"

    def __init__(self) -> None:
        self.requests: list = []

    def register(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return RegistrationResult(
            status=ProviderStatus.SUCCESS,
            external_customer_id="ext-1",
            external_customer_code="TT00042",
            message="ok",
            http_status=201,
        )


def _assert_lead_retained(harness: Harness, lead_id: str, expected_status: str) -> None:
    lead = harness.lead_row(lead_id)
    assert lead is not None, "the lead row must survive a provider failure"
    assert lead.registration_status.value == expected_status
    assert lead.tracking_token


def test_provider_unavailable_returns_202_pending_and_keeps_lead(
    make_harness,
) -> None:
    harness = make_harness(provider=UnavailableProvider())
    response = harness.post_registration()

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["registration_status"] == "PENDING"
    assert body["external_customer_id"] is None
    assert body["external_customer_code"] is None
    assert body["tracking_token"]
    assert body["login_url"] == "https://khachhang.viporder.com.vn/login"

    _assert_lead_retained(harness, body["lead_id"], "PENDING")
    lead = harness.lead_row(body["lead_id"])
    assert lead.last_error_code == "PROVIDER_UNAVAILABLE"
    assert lead.attempt_count == 1


def test_provider_timeout_returns_202_with_timeout_error_code(make_harness) -> None:
    harness = make_harness(provider=UnavailableProvider(http_status=None))
    response = harness.post_registration()

    assert response.status_code == 202
    lead = harness.lead_row(response.json()["lead_id"])
    assert lead.registration_status.value == "PENDING"
    assert lead.last_error_code == "PROVIDER_TIMEOUT"


def test_provider_exception_returns_202_and_keeps_lead(make_harness) -> None:
    provider = RaisingProvider()
    harness = make_harness(provider=provider)
    response = harness.post_registration()

    assert response.status_code == 202, response.text
    assert provider.calls == 1
    body = response.json()
    assert body["registration_status"] == "PENDING"
    _assert_lead_retained(harness, body["lead_id"], "PENDING")
    assert harness.lead_row(body["lead_id"]).last_error_code == "PROVIDER_ERROR"


def test_raised_provider_error_never_leaks_a_stack_trace(make_harness) -> None:
    harness = make_harness(provider=RaisingProvider("internal detail: /srv/db"))
    response = harness.post_registration()

    assert response.status_code == 202
    assert "Traceback" not in response.text
    assert "internal detail" not in response.text


@pytest.mark.parametrize("behaviour", ["unavailable", "timeout", "error"])
def test_mock_provider_failure_modes_all_keep_the_lead(make_harness, behaviour: str) -> None:
    harness = make_harness(mock_provider_behaviour=behaviour)
    response = harness.post_registration()

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["registration_status"] == "PENDING"
    assert body["tracking_token"]

    rows = harness.lead_rows()
    assert len(rows) == 1
    assert rows[0].lead_id == body["lead_id"]


def test_pending_lead_is_readable_with_its_tracking_token(make_harness) -> None:
    harness = make_harness(provider=UnavailableProvider())
    body = harness.post_registration().json()

    response = harness.client.get(
        f"/api/v1/registrations/{body['lead_id']}",
        headers={"X-Tracking-Token": body["tracking_token"]},
    )
    assert response.status_code == 200
    assert response.json()["registration_status"] == "PENDING"
    assert response.json()["external_customer_code"] is None


def test_provider_receives_the_canonical_phone_and_the_password(make_harness) -> None:
    provider = RecordingProvider()
    harness = make_harness(provider=provider)
    harness.post_registration(payload(phone="0912 345 678", password="hunter2hunter2"))

    assert len(provider.requests) == 1
    sent = provider.requests[0]
    assert sent.phone == "+84912345678"
    assert sent.password == "hunter2hunter2"
    assert sent.full_name == "Nguyễn Văn A"
    # ...and the request object must not print the password.
    assert "hunter2hunter2" not in repr(sent)


def test_provider_protocol_is_satisfied_by_the_adapters() -> None:
    from app.providers.mock import MockRegistrationProvider

    assert isinstance(MockRegistrationProvider("success"), RegistrationProvider)
    assert isinstance(UnavailableProvider(), RegistrationProvider)


# --- a broken adapter must not become a 5xx --------------------------------


class GarbageProvider:
    """Returns something that is not a RegistrationResult at all."""

    name = "garbage"

    def __init__(self, return_value: object) -> None:
        self.return_value = return_value
        self.calls = 0

    def register(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self.return_value


@pytest.mark.parametrize(
    "garbage",
    [object(), None, {"status": "SUCCESS"}, "SUCCESS", 42, []],
)
def test_a_provider_returning_a_non_result_does_not_500(make_harness, garbage: object) -> None:
    """An adapter bug is our bug, not the customer's. Keep the lead, answer 202."""
    provider = GarbageProvider(garbage)
    harness = make_harness(provider=provider)
    response = harness.post_registration()

    assert response.status_code == 202, response.text
    assert provider.calls == 1
    body = response.json()
    assert body["registration_status"] == "PENDING"
    assert body["tracking_token"]

    rows = harness.lead_rows()
    assert len(rows) == 1, "the lead must be retained"
    assert rows[0].last_error_code == "PROVIDER_CONTRACT_ERROR"
    assert rows[0].registration_status.value == "PENDING"


def test_a_provider_returning_a_result_with_a_bad_status_is_also_contained(
    make_harness,
) -> None:
    """A duck-typed result carrying a bare "SUCCESS" string must not be trusted.

    ``RegistrationResult`` validates its own status, so an honest instance can
    never hold this — the test forges one to prove that even a result with a
    look-alike status does not slip past the identity checks in `_apply_result`.
    The safe outcome is the UNAVAILABLE branch: retained PENDING, 202.
    """
    from app.providers.base import RegistrationResult

    class ConfusedProvider:
        name = "confused"

        def register(self, request):  # type: ignore[no-untyped-def]
            result = RegistrationResult(status=ProviderStatus.SUCCESS)
            object.__setattr__(result, "status", "SUCCESS")
            return result

    harness = make_harness(provider=ConfusedProvider())
    response = harness.post_registration()

    assert response.status_code == 202, response.text
    lead = harness.lead_rows()[0]
    assert lead.registration_status.value == "PENDING"
    assert lead.external_customer_code is None
