"""The provider's MEASURED success body, and the read-back that follows it (option A).

Measured 2026-10-09 (docs/KHAIBAO9610-INTEGRATION.md §10.8): `POST /register`
answers 200 with exactly `{"status": "success", "message": "..."}` — no code, no
id, no token. The account is created; the customer code is `customer.code`, and it
is returned by `POST /login` with the identity just registered (the same record is
also behind `GET /auth/profile`, which this adapter does not call).

Owner decision 2026-10-09 (option B): report REGISTERED with no code, never log in.
Owner decision 2026-11-09 (option A, supersedes B): log in ONCE as the customer and
read the code back, so the lead row, the 201 and the staff notification carry it.
If the read-back fails in any way, the answer is option B's: REGISTERED, no code.
"""

from __future__ import annotations

import json
import logging
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
LOGIN_BODY = FIXTURE["login"]["body"]
MEASURED_CODE = LOGIN_BODY["customer"]["code"]  # TT5233
MEASURED_ID = str(LOGIN_BODY["customer"]["id"])  # 6109

PASSWORD = "a-test-password-value-1234"  # nosec B105 - a test value
REQUEST = RegistrationRequest(
    full_name="Khách Kiểm Thử",
    phone="+84900000000",
    email="test@example.com",
    password=PASSWORD,
    confirm_password=PASSWORD,
    accept_terms=True,
)


class _Upstream:
    """Answers /register with the measured body and /login with ``login``."""

    def __init__(  # type: ignore[no-untyped-def]
        self, login, register_status: int = 200, register_body: object = None, login_path="/login"
    ):
        self.login = login
        self.login_path = login_path
        self.register_status = register_status
        self.register_body = MEASURED_BODY if register_body is None else register_body
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/register"):
            return httpx.Response(self.register_status, json=self.register_body, request=request)
        if request.url.path.endswith(self.login_path):
            if isinstance(self.login, Exception):
                raise self.login
            status, body = self.login
            if isinstance(body, bytes):
                return httpx.Response(status, content=body, request=request)
            return httpx.Response(status, json=body, request=request)
        raise AssertionError(f"unexpected call {request.method} {request.url.path}")

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def _provider(handler, **overrides) -> ViporderFrontendProvider:  # type: ignore[no-untyped-def]
    return ViporderFrontendProvider(
        mode="http",
        enable_real_calls=True,
        enable_real_registration=True,
        base_url="https://example.invalid/frontend/v1",
        user_agent="test-agent",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        **overrides,
    )


# --- the happy path -----------------------------------------------------------


def test_the_measured_body_is_followed_by_one_login_that_yields_the_code() -> None:
    upstream = _Upstream(login=(200, LOGIN_BODY))
    result = _provider(upstream).register(REQUEST)

    assert result.status is ProviderStatus.SUCCESS
    assert result.external_customer_code == MEASURED_CODE
    assert result.external_customer_id == MEASURED_ID
    assert result.retryable is False
    assert result.http_status == 200
    assert upstream.paths == ["/frontend/v1/register", "/frontend/v1/login"]


def test_the_login_is_the_measured_request_shape() -> None:
    """`{account, password}` as JSON, browser UA — the shape the 2026-10-09 tool used."""
    upstream = _Upstream(login=(200, LOGIN_BODY))
    _provider(upstream).register(REQUEST)

    login = upstream.requests[1]
    assert login.method == "POST"
    assert json.loads(login.content) == {"account": REQUEST.phone, "password": PASSWORD}
    assert login.headers["content-type"] == "application/json"
    assert login.headers["user-agent"] == "test-agent"


def test_the_login_path_is_configurable_and_must_be_absolute() -> None:
    upstream = _Upstream(login=(200, LOGIN_BODY), login_path="/auth/sign-in")
    _provider(upstream, login_path="/auth/sign-in").register(REQUEST)
    assert upstream.paths[1] == "/frontend/v1/auth/sign-in"

    from app.providers.base import ProviderConfigurationError

    with pytest.raises(ProviderConfigurationError, match="LOGIN_PATH"):
        _provider(upstream, login_path="login")
    with pytest.raises(ProviderConfigurationError, match="read_back_timeout"):
        _provider(upstream, read_back_timeout_seconds=0.1)


def test_a_register_body_that_already_carries_the_code_is_not_followed_by_a_login() -> None:
    upstream = _Upstream(
        login=(200, LOGIN_BODY),
        register_body={"data": {"customer_code": "TT00001", "customer_id": "1"}},
    )
    result = _provider(upstream).register(REQUEST)
    assert result.external_customer_code == "TT00001"
    assert upstream.paths == ["/frontend/v1/register"]


# --- the read-back fails: option B's answer, exactly one attempt -----------------


@pytest.mark.parametrize(
    "login",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        (500, {"message": "boom"}),
        (401, {"message": "Unauthorized"}),
        (429, {"message": "Too Many Attempts"}),
        (200, b"<html>not json</html>"),
        (200, {"status": "success"}),
        (200, {"access_token": "<redacted>", "customer": {"id": 6109}}),
        (200, {"code": 200, "message": "success"}),
    ],
    ids=[
        "connect-error",
        "timeout",
        "http-500",
        "http-401",
        "http-429",
        "not-json",
        "no-customer",
        "customer-without-code",
        "envelope-status-only",
    ],
)
def test_a_failed_read_back_is_still_a_success_without_a_code(login) -> None:  # type: ignore[no-untyped-def]
    upstream = _Upstream(login=login)
    result = _provider(upstream).register(REQUEST)

    assert result.status is ProviderStatus.SUCCESS, (
        "the account exists; the read-back is a convenience"
    )
    assert result.external_customer_code is None
    assert result.external_customer_id is None
    assert result.retryable is False
    # ONE login, never retried: an unmeasured rate limit is not worth a second trip.
    assert upstream.paths == ["/frontend/v1/register", "/frontend/v1/login"]


def test_the_read_back_never_logs_the_password_the_token_or_the_body(caplog) -> None:  # type: ignore[no-untyped-def]
    token_body = {**LOGIN_BODY, "access_token": "tok-SECRET-VALUE-xyz"}
    cases = [
        _Upstream(login=(200, token_body)),
        _Upstream(login=(500, {"echo": PASSWORD, "token": "tok-SECRET-VALUE-xyz"})),
        _Upstream(login=httpx.ConnectError(f"cannot reach /login with {PASSWORD}")),
    ]
    with caplog.at_level(logging.DEBUG):
        for upstream in cases:
            _provider(upstream).register(REQUEST)

    assert PASSWORD not in caplog.text
    assert "tok-SECRET-VALUE-xyz" not in caplog.text
    assert MEASURED_CODE not in caplog.text  # the code is customer data, not a log line


# --- the discriminator is unchanged -------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [{"status": "ok"}, {"message": "success"}, {"status": "failed"}, {"status": True}, []],
    ids=["status-ok", "message-success", "status-failed", "status-bool", "list"],
)
def test_only_the_measured_discriminator_counts(body: object) -> None:
    """Anything else that identifies nobody stays UNUSABLE_RESPONSE — and is NOT logged in to."""
    upstream = _Upstream(login=(200, LOGIN_BODY), register_body=body)
    result = _provider(upstream).register(REQUEST)
    assert result.status is ProviderStatus.UNUSABLE_RESPONSE
    assert upstream.paths == ["/frontend/v1/register"]


# --- through the service ------------------------------------------------------------


def test_the_lead_row_the_201_and_the_status_route_carry_the_code(make_harness) -> None:  # type: ignore[no-untyped-def]
    harness = make_harness(provider=_provider(_Upstream(login=(200, LOGIN_BODY))))

    response = harness.post_registration()

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["registration_status"] == "REGISTERED"
    assert body["external_customer_code"] == MEASURED_CODE
    assert body["external_customer_id"] == MEASURED_ID
    assert body["login_url"] == "https://khachhang.viporder.com.vn/login"
    (row,) = harness.lead_rows()
    assert row.registration_status is RegistrationStatus.REGISTERED
    assert row.external_customer_code == MEASURED_CODE
    assert row.external_customer_id == MEASURED_ID


def test_when_the_read_back_fails_the_lead_is_registered_without_a_code(make_harness) -> None:  # type: ignore[no-untyped-def]
    harness = make_harness(provider=_provider(_Upstream(login=(500, {"message": "boom"}))))

    response = harness.post_registration()

    assert response.status_code == 201, response.text
    assert response.json()["external_customer_code"] is None
    (row,) = harness.lead_rows()
    assert row.registration_status is RegistrationStatus.REGISTERED
    assert row.external_customer_code is None
