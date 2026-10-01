"""ViporderFrontendProvider against an httpx MockTransport.

Fully offline: no socket is ever opened. ``httpx.MockTransport`` intercepts the
request and hands the handler an ``httpx.Request``, so these tests assert both
the outcome mapping AND what the adapter actually put on the wire.

Reminder: the contract exercised here is a CANDIDATE taken from the owner's own
prior integration work. It is not a verified upstream contract, which is exactly
why it is unreachable unless two independent switches are set.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.providers.base import (
    ProviderConfigurationError,
    ProviderStatus,
    RegistrationRequest,
)
from app.providers.khaibao9610 import (
    ViporderFrontendProvider,
    looks_like_duplicate,
    strip_accents,
)

REQUEST = RegistrationRequest(
    full_name="Nguyễn Văn A",
    phone="+84912345678",
    password="secret-at-least-8",
    email="a@example.com",
    province="Bắc Ninh",
    service_interest="transport",
)

CONFIG = {
    "mode": "http",
    "enable_real_calls": True,
    "base_url": "https://apiviporder.com/frontend/v1",
    "timeout_seconds": 5.0,
    "user_agent": "Mozilla/5.0 (Test) AppleWebKit/537.36",
}


def build(handler, **overrides):  # type: ignore[no-untyped-def]
    config = {**CONFIG, **overrides}
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return ViporderFrontendProvider(client=client, **config)


# --- double opt-in ----------------------------------------------------------


def test_refuses_to_construct_without_explicit_real_call_opt_in() -> None:
    with pytest.raises(ProviderConfigurationError, match="ENABLE_REAL_CALLS"):
        ViporderFrontendProvider(
            mode="http",
            enable_real_calls=False,
            base_url=CONFIG["base_url"],
            user_agent=CONFIG["user_agent"],
        )


def test_refuses_to_construct_in_mock_mode() -> None:
    with pytest.raises(ProviderConfigurationError, match="KHAIBAO9610_MODE"):
        ViporderFrontendProvider(
            mode="mock",
            enable_real_calls=True,
            base_url=CONFIG["base_url"],
            user_agent=CONFIG["user_agent"],
        )


def test_refuses_an_absent_user_agent() -> None:
    with pytest.raises(ProviderConfigurationError, match="USER_AGENT"):
        ViporderFrontendProvider(
            mode="http",
            enable_real_calls=True,
            base_url=CONFIG["base_url"],
            user_agent="   ",
        )


@pytest.mark.parametrize("timeout", [0.5, 31.0, 120])
def test_refuses_a_timeout_outside_the_allowed_range(timeout: float) -> None:
    with pytest.raises(ProviderConfigurationError, match="TIMEOUT_SECONDS"):
        ViporderFrontendProvider(
            mode="http",
            enable_real_calls=True,
            base_url=CONFIG["base_url"],
            user_agent=CONFIG["user_agent"],
            timeout_seconds=timeout,
        )


def test_settings_defaults_to_mock_mode() -> None:
    from app.config import Settings

    assert Settings().khaibao9610_mode == "mock"
    assert Settings().khaibao9610_enable_real_calls is False


def test_factory_refuses_the_live_provider_without_opt_in() -> None:
    from app.config import Settings
    from app.providers.base import ProviderConfigurationError
    from app.providers.factory import build_provider

    settings = Settings(khaibao9610_mode="http", khaibao9610_enable_real_calls=False)
    with pytest.raises(ProviderConfigurationError):
        build_provider(settings)


def test_factory_returns_the_mock_by_default() -> None:
    from app.config import Settings
    from app.providers.factory import build_provider

    provider = build_provider(Settings())
    assert provider.name == "mock"


# --- outcome mapping --------------------------------------------------------


def test_success_maps_to_success_and_extracts_the_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"customer_id": "42", "customer_code": "TT00123", "message": "ok"},
        )

    result = build(handler).register(REQUEST)

    assert result.status is ProviderStatus.SUCCESS
    assert result.external_customer_id == "42"
    assert result.external_customer_code == "TT00123"
    assert result.http_status == 201
    assert result.retryable is False


def test_success_tolerates_a_nested_data_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"id": 7, "code": "TT00007"}})

    result = build(handler).register(REQUEST)
    assert result.status is ProviderStatus.SUCCESS
    assert (result.external_customer_id, result.external_customer_code) == ("7", "TT00007")


@pytest.mark.parametrize(
    "body",
    [
        "Số điện thoại đã tồn tại",
        "So dien thoai da duoc su dung",
        "This phone is already registered",
        "Phone number taken",
        "Tài khoản đã đăng ký",
        "Người dùng đã có tài khoản",
        "Customer already exists",
    ],
)
def test_422_with_duplicate_phrasing_maps_to_duplicate(body: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text=body)

    result = build(handler).register(REQUEST)
    assert result.status is ProviderStatus.DUPLICATE, body
    assert result.retryable is False


def test_422_without_duplicate_phrasing_maps_to_invalid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="Mật khẩu quá ngắn")

    result = build(handler).register(REQUEST)
    assert result.status is ProviderStatus.INVALID


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 405, 418])
def test_4xx_maps_to_invalid(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="nope")

    result = build(handler).register(REQUEST)
    assert result.status is ProviderStatus.INVALID
    assert result.http_status == status_code
    assert result.retryable is False


@pytest.mark.parametrize("status_code", [500, 502, 503, 504, 429])
def test_5xx_and_429_map_to_unavailable_and_are_retryable(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="down")

    result = build(handler).register(REQUEST)
    assert result.status is ProviderStatus.UNAVAILABLE
    assert result.retryable is True


def test_connection_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = build(handler).register(REQUEST)
    assert result.status is ProviderStatus.UNAVAILABLE
    assert result.retryable is True
    assert result.http_status is None


def test_timeout_maps_to_unavailable_and_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    result = build(handler).register(REQUEST)
    assert result.status is ProviderStatus.UNAVAILABLE
    assert result.retryable is True
    assert result.http_status is None


def test_unparseable_success_body_is_still_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, text="<html>thanks</html>")

    result = build(handler).register(REQUEST)
    assert result.status is ProviderStatus.SUCCESS
    assert result.external_customer_code is None


# --- what goes on the wire --------------------------------------------------


def test_request_shape_and_browser_user_agent() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"customer_code": "TT00001"})

    build(handler).register(REQUEST)

    assert captured["url"] == "https://apiviporder.com/frontend/v1/register"
    assert captured["body"] == {
        "name": "Nguyễn Văn A",
        "phone": "+84912345678",
        "email": "a@example.com",
        "password": "secret-at-least-8",
        "confirmPassword": "secret-at-least-8",
        "acceptTerms": True,
    }
    assert captured["headers"]["user-agent"].startswith("Mozilla/5.0")


def test_provider_never_logs_the_password(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    with caplog.at_level("DEBUG"):
        build(handler).register(REQUEST)

    assert "secret-at-least-8" not in caplog.text
    assert "password" not in caplog.text.lower()


# --- helpers ----------------------------------------------------------------


def test_strip_accents() -> None:
    assert strip_accents("Đã được sử dụng") == "da duoc su dung"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Số điện thoại đã tồn tại", True),
        ("already registered", True),
        ("Email này taken", True),
        ("Mật khẩu quá ngắn", False),
        ("", False),
    ],
)
def test_looks_like_duplicate(text: str, expected: bool) -> None:
    assert looks_like_duplicate(text) is expected
