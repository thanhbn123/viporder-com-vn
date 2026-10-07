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


# ---------------------------------------------------------------------------
# KEEP THE EVIDENCE — the fix for losing it once
# ---------------------------------------------------------------------------

SECRET = "a-test-password-value-1234"


def test_an_unparseable_body_is_carried_on_the_result() -> None:
    """The provider's actual body is the diagnostic question. It must survive the
    call instead of living only in a log that a container recreate will discard."""
    body = {"message": "ok", "unexpected": "shape", "reference": "ABC-123"}
    result = _provider(body).register(REQUEST)
    assert result.diagnostic_body, "the upstream body was not kept"
    assert "ABC-123" in result.diagnostic_body


def test_the_carried_body_is_REDACTED_of_the_password() -> None:
    """An upstream error body can echo the request. This text is written to the
    database and read by operators, so it must never contain the password."""
    echoed = {"message": f"rejected password {SECRET}"}
    result = _provider(echoed).register(REQUEST)
    assert SECRET not in (result.diagnostic_body or ""), (
        "the password reached the stored diagnostic body"
    )
    assert "<REDACTED>" in (result.diagnostic_body or "")


def test_the_carried_body_is_redacted_of_the_confirmation_too() -> None:
    result = _provider({"echo": SECRET}).register(REQUEST)
    assert SECRET not in (result.diagnostic_body or "")


def test_the_carried_body_is_bounded() -> None:
    """A diagnostic, not a data dump: it must stay inside String(500) once stored."""
    huge = {"message": "x" * 5000}
    result = _provider(huge).register(REQUEST)
    assert len(result.diagnostic_body or "") <= 500, len(result.diagnostic_body or "")


def test_a_body_of_braces_is_kept_as_evidence() -> None:
    """`{}` is not nothing — it is the provider literally returning an empty object,
    which is itself the answer to "what shape did it send?"."""
    result = _provider({}).register(REQUEST)
    assert result.diagnostic_body == "{}"


def test_a_truly_EMPTY_body_carries_nothing_rather_than_noise() -> None:
    """No body at all is the one case where an excerpt would be pure noise."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", request=request)

    provider = ViporderFrontendProvider(
        mode="http",
        enable_real_calls=True,
        enable_real_registration=True,
        base_url="https://example.invalid/frontend/v1",
        user_agent="test-agent",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.register(REQUEST)
    assert result.status is ProviderStatus.UNUSABLE_RESPONSE
    assert result.diagnostic_body == ""


def test_the_excerpt_helper_leaves_no_secret_behind() -> None:
    """Direct test of the helper, including a secret that appears twice."""
    from app.providers.khaibao9610 import _redacted_excerpt

    text = f"password={SECRET}&confirm={SECRET}&note=ok"
    out = _redacted_excerpt(text, SECRET, SECRET)
    assert SECRET not in out
    assert out.count("<REDACTED>") == 2


def test_the_service_stores_the_excerpt_on_the_lead(make_harness) -> None:
    """END TO END: an unparseable upstream body must be readable in the DATABASE.

    That is the whole point of the change. The evidence lost on 2026-10-07 lived
    only in a container log, and recreating the container destroyed it. A row
    survives.
    """
    from app.providers.base import ProviderStatus, RegistrationResult
    from tests.conftest import payload

    class _UnusableProvider:
        """A provider that answers 2xx and identifies nobody, exactly as observed."""

        name = "unusable-for-test"

        def register(self, request):  # type: ignore[no-untyped-def]
            return RegistrationResult(
                status=ProviderStatus.UNUSABLE_RESPONSE,
                message="The provider accepted the registration but returned no customer identifier we can read.",
                http_status=200,
                retryable=False,
                error_code="UNUSABLE_RESPONSE",
                diagnostic_body='{"message":"ok","provider_reference":"REF-999"}',
            )

    harness = make_harness(provider=_UnusableProvider(), khaibao9610_mode="mock")
    response = harness.post_registration(payload(phone="0912000777"))

    # The client is told the truth, not "success".
    assert response.status_code == 202, response.text
    assert response.json()["registration_status"] == "PENDING"

    # And the evidence is IN THE DATABASE, where the next operator can read it.
    rows = harness.lead_rows()
    assert len(rows) == 1, f"expected one lead, got {len(rows)}"
    row = rows[0]
    assert row.registration_status == "PENDING"
    assert row.last_error_code == "UNUSABLE_RESPONSE"
    assert row.last_error_message and "REF-999" in row.last_error_message, (
        f"the upstream body was not preserved: {row.last_error_message!r}"
    )
    # The redaction still applies on the way to the database.
    assert "a-test-password-value" not in (row.last_error_message or "")
