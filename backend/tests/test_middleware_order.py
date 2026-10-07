"""Middleware order, re-measured rather than carried forward as an assumption.

The order is load-bearing in both directions:

* ``SecurityHeaders`` must be OUTSIDE ``RequestSizeLimit`` and ``RateLimit``, so
  the 413 and 429 responses those produce are decorated too. A rejection that
  arrives without ``X-Content-Type-Options`` is still served to a browser.
* ``RateLimit`` must be OUTSIDE ``RequestSizeLimit``, so an oversized body still
  costs the caller an attempt. Otherwise a client can hammer the endpoint with
  100 MB bodies for free, and the size limit becomes the cheaper path.

These tests assert the composed stack, not the source order — ``add_middleware``
inserts at the front, so the last call is the outermost, which is easy to get
backwards by reading the code.
"""

from __future__ import annotations

import pytest
from starlette.middleware.cors import CORSMiddleware

from app.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from tests.conftest import payload

EXPECTED_INNER_STACK = [
    SecurityHeadersMiddleware,
    RequestContextMiddleware,
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
]


def _stack(app) -> list[type]:  # type: ignore[no-untyped-def]
    # user_middleware[0] is the outermost user middleware.
    return [middleware.cls for middleware in app.user_middleware]


def test_declared_order_is_security_context_rate_size(make_harness) -> None:
    harness = make_harness()
    assert _stack(harness.app) == EXPECTED_INNER_STACK


def test_cors_is_outermost_only_when_enabled(make_harness) -> None:
    without = make_harness()
    assert CORSMiddleware not in _stack(without.app)

    with_cors = make_harness(cors_allow_origins="https://viporder.com.vn")
    stack = _stack(with_cors.app)
    assert stack[0] is CORSMiddleware
    assert stack[1:] == EXPECTED_INNER_STACK


def test_the_413_response_is_decorated_by_the_outer_middlewares(make_harness) -> None:
    """Behavioural proof that SecurityHeaders/RequestContext wrap SizeLimit."""
    harness = make_harness(rate_limit_enabled=False, max_request_bytes=1024)

    response = harness.client.post(
        "/api/v1/registrations",
        content=b"x" * 8192,
        headers={"Content-Type": "application/json", "X-Request-Id": "size-limit-probe"},
    )

    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-request-id"] == "size-limit-probe"


def test_the_429_response_is_decorated_by_the_outer_middlewares(make_harness) -> None:
    harness = make_harness(rate_limit_enabled=True, rate_limit_attempts=1)

    assert harness.post_registration(payload(phone="0912000011")).status_code == 201
    blocked = harness.post_registration(
        payload(phone="0912000012"), headers={"X-Request-Id": "rate-limit-probe"}
    )

    assert blocked.status_code == 429
    assert blocked.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in blocked.headers["content-security-policy"]
    assert blocked.headers["x-request-id"] == "rate-limit-probe"


# --- an oversized body still costs an attempt -------------------------------


def test_an_oversized_body_consumes_a_rate_limit_attempt(make_harness) -> None:
    """413 first, then 429 — the size limit is not a free path past the limiter."""
    harness = make_harness(
        rate_limit_enabled=True,
        rate_limit_attempts=1,
        max_request_bytes=1024,
    )

    first = harness.client.post(
        "/api/v1/registrations",
        content=b"x" * 8192,
        headers={"Content-Type": "application/json"},
    )
    second = harness.client.post(
        "/api/v1/registrations",
        content=b"x" * 8192,
        headers={"Content-Type": "application/json"},
    )

    assert first.status_code == 413, first.text
    assert second.status_code == 429, second.text
    assert second.json()["error"]["code"] == "RATE_LIMITED"
    assert int(second.headers["Retry-After"]) >= 1


def test_the_size_limit_cannot_be_used_to_probe_without_limit(make_harness) -> None:
    """With N attempts allowed, N+1 oversized bodies are refused as 429."""
    harness = make_harness(
        rate_limit_enabled=True,
        rate_limit_attempts=3,
        max_request_bytes=1024,
    )

    statuses = [
        harness.client.post(
            "/api/v1/registrations",
            content=b"x" * 8192,
            headers={"Content-Type": "application/json"},
        ).status_code
        for _ in range(4)
    ]

    assert statuses == [413, 413, 413, 429]


def test_a_normal_request_consumes_an_attempt_too(make_harness) -> None:
    """The limiter counts attempts, not rejections — including successful ones."""
    harness = make_harness(
        rate_limit_enabled=True, rate_limit_attempts=1, max_request_bytes=64 * 1024
    )

    assert harness.post_registration(payload(phone="0912000011")).status_code == 201
    assert harness.post_registration(payload(phone="0912000012")).status_code == 429


@pytest.mark.parametrize("rate_limit_enabled", [True, False], ids=["limit-on", "limit-off"])
def test_a_413_is_413_whether_or_not_the_limiter_is_on(
    make_harness, rate_limit_enabled: bool
) -> None:
    """The size limit does not depend on the rate limiter being enabled."""
    harness = make_harness(
        rate_limit_enabled=rate_limit_enabled, rate_limit_attempts=99, max_request_bytes=1024
    )
    response = harness.client.post(
        "/api/v1/registrations",
        content=b"x" * 8192,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_rate_limiter_state_is_per_app_instance(make_harness) -> None:
    """Two harnesses must not share a window — otherwise tests bleed into each other."""
    first = make_harness(rate_limit_enabled=True, rate_limit_attempts=1)
    second = make_harness(rate_limit_enabled=True, rate_limit_attempts=1)

    assert first.post_registration(payload(phone="0912000011")).status_code == 201
    assert first.post_registration(payload(phone="0912000012")).status_code == 429
    # A fresh app starts from an empty window.
    assert second.post_registration(payload(phone="0912000013")).status_code == 201
