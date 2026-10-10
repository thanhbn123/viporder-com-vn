"""The provider's MEASURED success body is a success without a code (option B).

Measured 2026-10-09 (docs/KHAIBAO9610-INTEGRATION.md §10.8): `POST /register`
answers 200 with exactly `{"status": "success", "message": "..."}`. The account is
created; the customer code (`customer.code`) appears only after the customer signs
in. Before this change the backend classified that body `UNUSABLE_RESPONSE`, left
the lead PENDING and told a customer whose account EXISTED that staff would call —
and an operator retry would then have hit a duplicate.

Owner decision 2026-10-09: report it as REGISTERED with no code, and tell the
customer to sign in to the portal to see it. The backend does NOT log in as the
customer.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.models import RegistrationStatus
from app.providers.base import ProviderStatus, RegistrationRequest
from app.providers.khaibao9610 import ViporderFrontendProvider

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "live_registration_20261009.json").read_text(
        encoding="utf-8"
    )
)
MEASURED_BODY = FIXTURE["register"]["body"]

REQUEST = RegistrationRequest(
    full_name="Khách Kiểm Thử",
    phone="+84900000000",
    email="test@example.com",
    password="a-test-password-value-1234",
    confirm_password="a-test-password-value-1234",
    accept_terms=True,
)


class _Recorder:
    def __init__(self, status: int, body: object) -> None:
        self.status, self.body, self.paths = status, body, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        return httpx.Response(self.status, json=self.body, request=request)


def _provider(handler) -> ViporderFrontendProvider:  # type: ignore[no-untyped-def]
    return ViporderFrontendProvider(
        mode="http",
        enable_real_calls=True,
        enable_real_registration=True,
        base_url="https://example.invalid/frontend/v1",
        user_agent="test-agent",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_the_measured_body_is_a_success_without_a_code() -> None:
    recorder = _Recorder(200, MEASURED_BODY)
    result = _provider(recorder).register(REQUEST)

    assert result.status is ProviderStatus.SUCCESS
    assert result.external_customer_code is None
    assert result.external_customer_id is None
    assert result.retryable is False
    assert result.http_status == 200


def test_the_backend_never_logs_in_as_the_customer() -> None:
    """Option B, not A: exactly one call, to /register — no /login, no /auth/profile."""
    recorder = _Recorder(200, MEASURED_BODY)
    _provider(recorder).register(REQUEST)
    assert recorder.paths == ["/frontend/v1/register"]


@pytest.mark.parametrize(
    "body",
    [{"status": "ok"}, {"message": "success"}, {"status": "failed"}, {"status": True}, []],
    ids=["status-ok", "message-success", "status-failed", "status-bool", "list"],
)
def test_only_the_measured_discriminator_counts(body: object) -> None:
    """Anything else that identifies nobody stays UNUSABLE_RESPONSE."""
    result = _provider(_Recorder(200, body)).register(REQUEST)
    assert result.status is ProviderStatus.UNUSABLE_RESPONSE


def test_the_lead_is_registered_and_the_customer_is_told_to_sign_in(make_harness) -> None:
    harness = make_harness(provider=_provider(_Recorder(200, MEASURED_BODY)))

    response = harness.post_registration()

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["registration_status"] == "REGISTERED"
    assert body["external_customer_code"] is None
    assert body["login_url"] == "https://khachhang.viporder.com.vn/login"
    (row,) = harness.lead_rows()
    assert row.registration_status is RegistrationStatus.REGISTERED
    assert row.external_customer_code is None
