"""Security and transport middleware.

All three are written as raw ASGI middleware rather than
``BaseHTTPMiddleware``. Reason: they must be able to reject a request *before*
the body is buffered (64 KiB limit) and must be able to answer when the
downstream app never produces a response at all. A raw ASGI wrapper also lets
us replay the body we already read instead of losing it.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

REGISTRATIONS_PATH = "/api/v1/registrations"

#: Default-deny. Tightened deliberately: the API serves JSON only, so nothing
#: needs inline script, remote frames, plugins or form submissions.
DEFAULT_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'"
)

PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), payment=(), usb=()"
)


def client_ip(scope: Scope, *, trust_proxy_headers: bool) -> str:
    """Resolve the caller's IP.

    ``X-Forwarded-For`` is client-controlled unless a trusted proxy sets it, so
    it is only consulted when ``TRUST_PROXY_HEADERS=yes``. With the default
    (``no``) a caller cannot forge their IP to escape the rate limiter.
    """
    if trust_proxy_headers:
        headers = Headers(scope=scope)
        forwarded = headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
    client = scope.get("client")
    if client:
        return str(client[0])
    return "unknown"


class RequestSizeLimitMiddleware:
    """Reject bodies larger than ``max_bytes`` with 413."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass  # Unparseable length: fall through to counting bytes.

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        replayed = False
        buffered = bytes(body)

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        logger.warning(
            "request rejected: body exceeds %s bytes (path=%s)",
            self.max_bytes,
            scope.get("path"),
        )
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": f"Request body must not exceed {self.max_bytes} bytes.",
                }
            },
        )
        await response(scope, receive, send)


class RateLimitMiddleware:
    """In-memory sliding-window rate limiter for registration attempts.

    Scope: per client IP, per process. That is adequate for a single instance
    and deliberately simple. **Path to Redis**: replace ``self._hits`` with a
    Redis sorted set per key (``ZADD`` now / ``ZREMRANGEBYSCORE`` older than the
    window / ``ZCARD``), which makes the limit shared across replicas. The
    middleware boundary does not change.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        max_attempts: int = 10,
        window_seconds: int = 600,
        trust_proxy_headers: bool = False,
        path: str = REGISTRATIONS_PATH,
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.trust_proxy_headers = trust_proxy_headers
        self.path = path
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _retry_after(self, key: str, now: float) -> int:
        with self._lock:
            hits = self._hits[key]
            cutoff = now - self.window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) < self.max_attempts:
                hits.append(now)
                return 0
            return max(1, int(self.window_seconds - (now - hits[0])) + 1)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            not self.enabled
            or scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != self.path
        ):
            await self.app(scope, receive, send)
            return

        key = client_ip(scope, trust_proxy_headers=self.trust_proxy_headers)
        retry_after = self._retry_after(key, time.monotonic())
        if retry_after:
            logger.warning("rate limit hit for %s on %s", key, self.path)
            response = JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": ("Too many registration attempts. Please try again later."),
                    }
                },
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class RequestContextMiddleware:
    """Attach a correlation id to every request and echo it back."""

    def __init__(self, app: ASGIApp, *, header_name: str = "X-Request-Id") -> None:
        self.app = app
        self.header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id")
        request_id = _clean_request_id(incoming) or uuid.uuid4().hex
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).setdefault(self.header_name, request_id)
            await send(message)

        await self.app(scope, receive, send_wrapper)


class SecurityHeadersMiddleware:
    """Attach the baseline security headers to every response.

    HSTS is only sent over HTTPS: emitting it from a plain-HTTP dev server would
    pin the browser to a scheme that is not actually served.
    """

    def __init__(
        self, app: ASGIApp, *, trust_proxy_headers: bool = False, hsts_max_age: int = 31536000
    ) -> None:
        self.app = app
        self.trust_proxy_headers = trust_proxy_headers
        self.hsts_max_age = hsts_max_age

    def _headers_for(self, scope: Scope, *, include_hsts: bool) -> dict[str, str]:
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Content-Security-Policy": DEFAULT_CSP,
            "Permissions-Policy": PERMISSIONS_POLICY,
            "Cross-Origin-Resource-Policy": "same-origin",
        }
        if include_hsts:
            # NO `includeSubDomains`. Measured 2026-10-02: with it, an /api/*
            # response carried it to the client while nginx sent the withheld
            # form — TWO headers, and one of them pins every subdomain to HTTPS
            # for a year.
            #
            # HSTS is HOST-scoped, not path-scoped, so an API response can bind
            # the marketing apex. That defeats the whole staged decision: the
            # reason `includeSubDomains` is withheld is that
            # `khachhang.viporder.com.vn` shares this host's IP and the apex
            # certificate does not cover it, so a TLS mismatch there would become
            # a error a returning customer cannot click through.
            #
            # The application should not be deciding this at all; nginx owns it.
            # This is the belt to nginx's `proxy_hide_header` braces.
            headers["Strict-Transport-Security"] = f"max-age={self.hsts_max_age}"
        return headers

    def _is_https(self, scope: Scope) -> bool:
        if scope.get("scheme") == "https":
            return True
        if self.trust_proxy_headers:
            proto = Headers(scope=scope).get("x-forwarded-proto", "")
            return proto.split(",")[0].strip().lower() == "https"
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = self._headers_for(scope, include_hsts=self._is_https(scope))

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(scope=message)
                for name, value in headers.items():
                    mutable.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _clean_request_id(value: str | None) -> str | None:
    """Accept a client-supplied correlation id only if it is safe to echo.

    An unvalidated header echoed into a log line is a log-injection primitive.
    """
    if not value:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 64:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if not set(candidate) <= allowed:
        return None
    return candidate


def json_bytes(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")
