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
    ProviderResponseError,
    ProviderStatus,
    ProviderUnavailableError,
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
    confirm_password="secret-at-least-8",
    accept_terms=True,
    email="a@example.com",
    province="Bắc Ninh",
    service_interest="transport",
)

# This file exercises the LIVE adapter against an httpx.MockTransport — it is not
# reaching the network. It therefore has to opt in to BOTH capabilities explicitly:
# `enable_real_calls` permits tracking lookups, and `enable_real_registration`
# permits customer creation. They are separate switches on purpose, so that a test
# that only wants to read cannot create anybody.
#
# These tests previously set only `enable_real_calls` and called `register()`,
# which is precisely the coupling that let a staging tracking test POST a real
# registration to the provider's production API.
CONFIG = {
    "mode": "http",
    "enable_real_calls": True,
    "enable_real_registration": True,
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


def test_an_unparseable_2xx_body_is_NOT_reported_as_success() -> None:
    """CHANGED 2026-10-07, on measured evidence, from the opposite assertion.

    This test used to require `SUCCESS` for a 2xx whose body identified nobody. That
    was defensible while the live contract was unknown: a provider might accept a
    registration and return nothing useful on purpose.

    The owner-authorized one-shot live POST settled it the other way. The provider
    answered 2xx and `_extract` found **no customer id and no customer code
    anywhere**, so the customer was told "Đăng ký thành công" and handed **no code** —
    the one thing registering is for. Nothing anywhere was loud about it.

    A body that identifies nobody is therefore its own condition, `UNUSABLE_RESPONSE`,
    and **not retryable**: the request may well have created an account, so an
    automatic retry could double-register somebody.

    The old assertion is kept in words above rather than deleted, because the reason
    it changed is the useful part.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, text="<html>thanks</html>")

    result = build(handler).register(REQUEST)
    assert result.status is ProviderStatus.UNUSABLE_RESPONSE
    assert result.error_code == "UNUSABLE_RESPONSE"
    assert result.retryable is False
    assert result.http_status == 201, "the upstream status is evidence and must survive"
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


def test_accept_terms_is_the_submitted_value_not_a_hard_coded_true() -> None:
    """The adapter used to send ``acceptTerms: True`` unconditionally.

    A request carrying a real ``accept_terms`` value proves the body now reflects
    the submission rather than an assertion made on the customer's behalf. (The
    schema refuses ``False`` before this point, so a `False` here can only be
    reached by constructing the request directly — which is the point of the
    test.)
    """
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"customer_code": "TT00001"})

    request = RegistrationRequest(
        full_name="A",
        phone="+84912345678",
        password="secret-at-least-8",
        confirm_password="secret-at-least-8",
        accept_terms=False,
    )
    build(handler).register(request)

    assert captured["body"]["acceptTerms"] is False


def test_provider_sends_the_submitted_confirmation_not_the_password() -> None:
    """Falsifies the exact previous bug: ``"confirmPassword": request.password``.

    The request object enforces that the two are equal, so through the public
    path they are indistinguishable — which is precisely why the old line was
    invisible for so long. This test bypasses that invariant on purpose so the
    adapter has to read the field it claims to read. If someone reintroduces
    ``request.password`` here, this fails.
    """
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"customer_code": "TT00001"})

    request = RegistrationRequest(
        full_name="A",
        phone="+84912345678",
        password="secret-at-least-8",
        confirm_password="secret-at-least-8",
        accept_terms=True,
    )
    # Deliberately break the invariant to make the two values distinguishable.
    object.__setattr__(request, "confirm_password", "distinct-confirm-9")

    build(handler).register(request)

    assert captured["body"]["confirmPassword"] == "distinct-confirm-9"
    assert captured["body"]["password"] == "secret-at-least-8"


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


# --- an outage is not a timeout --------------------------------------------


def test_connect_error_is_labelled_unreachable_not_timeout() -> None:
    """Both arrive with http_status None, so the code has to carry the meaning.

    Calling a refused connection a TIMEOUT makes a real outage undiagnosable
    from the stored lead.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = build(handler).register(REQUEST)

    assert result.status is ProviderStatus.UNAVAILABLE
    assert result.error_code == "PROVIDER_UNREACHABLE"
    assert result.http_status is None


def test_timeout_is_labelled_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    result = build(handler).register(REQUEST)
    assert result.error_code == "PROVIDER_TIMEOUT"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (500, "PROVIDER_UNAVAILABLE"),
        (502, "PROVIDER_UNAVAILABLE"),
        (503, "PROVIDER_UNAVAILABLE"),
        (504, "PROVIDER_UNAVAILABLE"),
        (429, "PROVIDER_RATE_LIMITED"),
    ],
)
def test_http_level_unavailability_carries_a_specific_code(status_code: int, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="down")

    assert build(handler).register(REQUEST).error_code == expected


def test_unavailable_codes_are_all_distinct() -> None:
    """The three failure classes must not collapse into one another."""

    def connect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    codes = {
        build(connect).register(REQUEST).error_code,
        build(timeout).register(REQUEST).error_code,
        build(server_error).register(REQUEST).error_code,
    }
    assert codes == {"PROVIDER_UNREACHABLE", "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"}


# --- tracking lookups: the two envelopes differ -----------------------------
#
# Measured live: /warehouse-imports/{keyword} answers with a BARE object while
# /package-sealings/{keyword} answers with {"data": {...}}. The provider's job is
# to hand back the RAW body and NOT to decide which shape arrived — that decision
# belongs to app/tracking.py, in one place. These tests pin the raw pass-through,
# including the fact that the wrapping is still there.


def test_tracking_returns_the_bare_object_untouched() -> None:
    bare = {"id": 9, "customer_code": "TT1", "china_tracking_code": "KY1"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "https://apiviporder.com/frontend/v1/warehouse-imports/KY4001103376087-2-4-%7Cs"
        )
        return httpx.Response(200, json=bare)

    assert build(handler).find_warehouse_import("KY4001103376087-2-4-|s") == bare


def test_tracking_returns_the_wrapped_envelope_without_unwrapping() -> None:
    """The sealing body must come back still wrapped.

    Unwrapping in the adapter is exactly how the two shapes get conflated, so the
    assertion is that the ``data`` key is still present at this layer.
    """
    envelope = {"data": {"id": 4, "code": "SEAL-1"}}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ("https://apiviporder.com/frontend/v1/package-sealings/SEAL-1")
        return httpx.Response(200, json=envelope)

    result = build(handler).find_package_sealing("SEAL-1")

    assert result == envelope
    assert "data" in result


def test_both_tracking_endpoints_use_different_paths() -> None:
    """A copy-paste that pointed both methods at one path would be invisible."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    provider = build(handler)
    provider.find_warehouse_import("KY1")
    provider.find_package_sealing("KY1")

    assert len(set(seen)) == 2, seen


def test_tracking_404_is_none_not_an_error() -> None:
    """The measured "this code does not exist" answer is a result, not a failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Mã vận đơn không tồn tại"})

    assert build(handler).find_warehouse_import("KY4001103376087") is None
    assert build(handler).find_package_sealing("KY4001103376087") is None


@pytest.mark.parametrize(
    "keyword",
    ["KY4001103376087-2-4-|s", "a/b", "a?b=c", "a#frag", "a b", "100%"],
)
def test_tracking_percent_encodes_the_path_segment(keyword: str) -> None:
    """Nothing a keyword contains may add a segment, a query, or a fragment.

    Asserted on ``raw_path``, not ``path``: httpx *decodes* percent-escapes in
    ``url.path``, so ``a%2Fb`` reads back as ``a/b`` there and an assertion on it
    would be testing httpx's decoder rather than the bytes on the wire. This was
    found by the test failing, and the correction is to look at the wire form.
    """
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        return httpx.Response(200, json={})

    build(handler).find_warehouse_import(keyword)

    url = captured["url"]
    assert url.query == b"", "a keyword must not be able to start a query string"
    assert url.fragment == "", "a keyword must not be able to add a fragment"

    raw = url.raw_path.decode("ascii")
    assert "?" not in raw and "#" not in raw
    # The whole keyword stayed INSIDE one path segment: the segment count under
    # /frontend/v1 is unchanged at 4 (frontend, v1, warehouse-imports, keyword).
    assert len(raw.strip("/").split("/")) == 4, raw


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_tracking_5xx_raises_a_response_error(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="boom")

    with pytest.raises(ProviderResponseError):
        build(handler).find_warehouse_import("KY1")


@pytest.mark.parametrize("status_code", [401, 403, 400, 405])
def test_tracking_unexpected_4xx_raises_a_response_error(status_code: int) -> None:
    """Auth was not needed AT MEASUREMENT TIME.

    That is an observation, not a guarantee. If the upstream starts answering 401
    the contract has changed and someone has to look — so it is reported as a bad
    response rather than quietly presented to the customer as "not found".
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "Unauthenticated."})

    with pytest.raises(ProviderResponseError):
        build(handler).find_warehouse_import("KY1")


def test_tracking_429_raises_unavailable_not_a_bad_response() -> None:
    """429 is transient capacity, not a broken contract, so the codes differ."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    with pytest.raises(ProviderUnavailableError):
        build(handler).find_warehouse_import("KY1")


def test_tracking_timeout_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ProviderUnavailableError):
        build(handler).find_package_sealing("SEAL-1")


def test_tracking_connect_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderUnavailableError):
        build(handler).find_warehouse_import("KY1")


@pytest.mark.parametrize(
    "body",
    [
        "<html>nope</html>",
        "",
        '{"unterminated": ',
    ],
)
def test_tracking_malformed_json_raises_a_response_error(body: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    with pytest.raises(ProviderResponseError):
        build(handler).find_warehouse_import("KY1")


@pytest.mark.parametrize("body", ["[]", '"a string"', "12", "null"])
def test_tracking_json_that_is_not_an_object_raises_a_response_error(body: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    with pytest.raises(ProviderResponseError):
        build(handler).find_warehouse_import("KY1")


def test_tracking_tolerates_a_wrong_content_type() -> None:
    """The body is what matters, not the header.

    Laravel and Cloudflare both emit ``text/html`` on some JSON error paths; a
    strict content-type check would turn a usable 200 into a 502.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text='{"id": 1}', headers={"content-type": "text/html; charset=utf-8"}
        )

    assert build(handler).find_warehouse_import("KY1") == {"id": 1}


def test_tracking_refuses_a_response_over_the_size_cap() -> None:
    """A third party must not be able to make one response exhaust a worker."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"pad": "' + b"x" * 5000 + b'"}')

    with pytest.raises(ProviderResponseError, match="bytes"):
        build(handler, max_response_bytes=1024).find_warehouse_import("KY1")


def test_tracking_sends_a_browser_user_agent() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={})

    build(handler).find_warehouse_import("KY1")

    assert captured["headers"]["user-agent"].startswith("Mozilla/5.0")
    # A bodyless GET must not describe a body it does not have.
    assert "content-type" not in captured["headers"]


def test_tracking_makes_exactly_one_request_and_never_retries() -> None:
    """No retries, on purpose.

    A GET may only be retried if its idempotency is *proven*, and the owner's
    measurement did not establish that for these endpoints. This test counts the
    attempts so that a retry cannot be added without a visible decision.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(503, text="down")

    with pytest.raises(ProviderResponseError):
        build(handler).find_warehouse_import("KY1")

    assert len(calls) == 1


def test_tracking_template_must_contain_a_keyword_placeholder() -> None:
    with pytest.raises(ProviderConfigurationError, match="keyword"):
        build(lambda request: httpx.Response(200, json={}), warehouse_import_path="/fixed")


def test_tracking_template_must_be_absolute() -> None:
    with pytest.raises(ProviderConfigurationError, match="start with"):
        build(lambda request: httpx.Response(200, json={}), package_sealing_path="nope/{keyword}")


def test_provider_never_logs_the_confirmation_either(caplog) -> None:
    """Both password values are secrets; the log must be free of both."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    with caplog.at_level("DEBUG"):
        build(handler).register(REQUEST)

    assert "secret-at-least-8" not in caplog.text
