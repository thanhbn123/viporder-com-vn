"""G12 — a 2xx that identifies nobody must not be a silent success.

MEASURED with the owner-authorized one-shot live POST, 2026-10-07 05:53:38 UTC:

    HTTP 201 from our app, registration_status REGISTERED
    external_customer_id   = null
    external_customer_code = null

The provider accepted the registration, and `_extract` found **no customer id and
no customer code anywhere** — it searches the top level plus `data`/`customer`/
`result`, against `customer_code | customerCode | code | ma_khach_hang` and
`customer_id | customerId | id | user_id`.

The customer code is the point of registering. Telling somebody "Đăng ký thành công"
and handing them nothing is a silent failure, so a body that identifies nobody is now
its own condition: `UNUSABLE_RESPONSE`, not retryable, stored on the lead.

A NOTE ON THE FIXTURES, because it matters. The provider's RAW body was **not
captured** — the container was recreated to disarm the write switch, which discarded
the log holding it. So these fixtures encode the **observed property** (2xx, nothing
extractable) and are NOT the observed bytes. Claiming otherwise would be exactly the
kind of invented evidence this project keeps having to remove.
"""

from __future__ import annotations

import httpx
import pytest

from app.providers.base import ProviderStatus, RegistrationRequest
from app.providers.khaibao9610 import ViporderFrontendProvider

REQUEST = RegistrationRequest(
    full_name="VIPORDER NGHIEM THU",
    phone="0968961962",
    email="qq968961962@gmail.com",
    password="a-test-password-value-1234",
    confirm_password="a-test-password-value-1234",
    accept_terms=True,
)


def _provider(body: object, status: int = 200) -> ViporderFrontendProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, request=request)

    return ViporderFrontendProvider(
        mode="http",
        enable_real_calls=True,
        enable_real_registration=True,
        base_url="https://example.invalid/frontend/v1",
        user_agent="test-agent",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


# ---------------------------------------------------------------------------
# The regression: 2xx with nothing identifiable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"message": "success"},  # a message and nothing else
        {"data": {}},  # an empty envelope
        {"status": "ok", "data": {"message": "ok"}},  # ok-looking, identifies nobody
        {},  # literally empty
    ],
    ids=["message-only", "empty-data", "status-ok", "empty-object"],
)
def test_a_2xx_that_identifies_nobody_is_NOT_success(body: object) -> None:
    result = _provider(body).register(REQUEST)
    assert result.status is ProviderStatus.UNUSABLE_RESPONSE, (
        f"{body!r} was reported as {result.status} — a caller cannot tell it apart "
        f"from a real registration, and the customer gets no code"
    )
    assert result.error_code == "UNUSABLE_RESPONSE"


def test_an_unusable_response_is_never_retryable() -> None:
    """The request may well have created an account, so a retry could
    double-register somebody. Nothing upstream may try again on its own."""
    result = _provider({"message": "success"}).register(REQUEST)
    assert result.retryable is False


def test_an_unusable_response_still_reports_the_upstream_status() -> None:
    """The HTTP status is evidence; it must survive the classification."""
    result = _provider({"message": "success"}, status=201).register(REQUEST)
    assert result.http_status == 201


# ---------------------------------------------------------------------------
# The positive controls: a readable body must still behave exactly as before
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        ({"customer_code": "TT00123"}, "TT00123"),
        ({"customerCode": "TT00124"}, "TT00124"),
        ({"code": "TT00125"}, "TT00125"),
        ({"ma_khach_hang": "TT00126"}, "TT00126"),
        ({"data": {"customer_code": "TT00127"}}, "TT00127"),
        ({"customer": {"code": "TT00128"}}, "TT00128"),
        ({"result": {"customer_code": "TT00129"}}, "TT00129"),
    ],
    ids=["customer_code", "camel", "code", "vietnamese", "data", "customer", "result"],
)
def test_every_documented_key_still_yields_a_success(body: object, expected_code: str) -> None:
    result = _provider(body).register(REQUEST)
    assert result.status is ProviderStatus.SUCCESS
    assert result.external_customer_code == expected_code


def test_an_id_alone_is_enough_to_be_a_success() -> None:
    """An id with no code is still a usable success — the customer is identified."""
    result = _provider({"id": 42}).register(REQUEST)
    assert result.status is ProviderStatus.SUCCESS
    assert result.external_customer_id == "42"
    assert result.external_customer_code is None
