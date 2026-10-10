"""Application factory.

Everything the app needs is constructed here and parked on ``app.state``, so a
test can build an app with a fake provider, a temp database and rate limiting
switched off — without monkeypatching import state anywhere.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import LOGIN_URL, Settings, get_settings
from .db import Database
from .errors import (
    INTERNAL_ERROR,
    ApiError,
    api_error_handler,
    error_response,
    http_exception_handler,
    validation_error_handler,
)
from .logging_filters import configure_logging
from .middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from .notifications import ZaloNotifier
from .providers.base import RegistrationProvider
from .providers.factory import build_provider
from .repositories.sqlalchemy_repo import SqlAlchemyLeadRepository
from .routers import admin, health, registrations, tracking

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    provider: RegistrationProvider | None = None,
    database: Database | None = None,
    create_schema: bool | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    database = database or Database.from_settings(settings)
    provider = provider or build_provider(settings)

    # /docs and /openapi.json enumerate every route, parameter and error code.
    # That is useful in development and a map for an attacker in production, so
    # they are gated: off when APP_ENV=production unless an operator explicitly
    # overrides ENABLE_API_DOCS. ReDoc is never served.
    docs_enabled = settings.api_docs_enabled

    app = FastAPI(
        title="VIPORDER.COM.VN registration API",
        version=settings.app_version,
        docs_url="/api/docs" if docs_enabled else None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
        redoc_url=None,
    )

    app.state.settings = settings
    app.state.database = database
    app.state.provider = provider
    app.state.notifier = ZaloNotifier(settings)
    app.state.repository_factory = lambda: SqlAlchemyLeadRepository(database.session())

    should_create = settings.auto_create_schema if create_schema is None else create_schema
    if should_create:
        database.create_all()

    # --- Middleware ---------------------------------------------------------
    # add_middleware inserts at the front, so the LAST call is the OUTERMOST.
    # Order below (inner -> outer): size limit, rate limit, request context,
    # security headers, CORS. Security headers therefore also decorate the
    # 413/429 responses produced by the middleware inside it.
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        RateLimitMiddleware,
        enabled=settings.rate_limit_enabled,
        max_attempts=settings.rate_limit_attempts,
        window_seconds=settings.rate_limit_window_seconds,
        trust_proxy_headers=settings.trust_proxy_headers,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        SecurityHeadersMiddleware,
        trust_proxy_headers=settings.trust_proxy_headers,
    )

    origins = settings.cors_origins
    if origins:
        # Never "*" together with credentials. An explicit list gets credentials;
        # a wildcard does not, because the combination is rejected by browsers
        # and is a well-known footgun.
        allow_all = "*" in origins
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"] if allow_all else origins,
            allow_credentials=not allow_all,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "Idempotency-Key", "X-Request-Id"],
        )

    # --- Error handling -----------------------------------------------------
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _internal_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]

    # --- Routes -------------------------------------------------------------
    app.include_router(health.router)
    app.include_router(registrations.router)
    app.include_router(tracking.router)
    app.include_router(admin.router)

    logger.info(
        "viporder backend ready provider=%s mode=%s login_url=%s",
        provider.name,
        settings.khaibao9610_mode,
        LOGIN_URL,
    )
    return app


async def _internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defence: log the traceback, tell the client nothing."""
    request_id = ""
    if hasattr(request, "state"):
        request_id = getattr(request.state, "request_id", "") or ""
    logger.exception(
        "unhandled error request_id=%s path=%s type=%s",
        request_id or "-",
        request.url.path,
        type(exc).__name__,
    )
    return error_response(
        500,
        INTERNAL_ERROR,
        "An internal error occurred. Please try again later.",
        headers={"X-Request-Id": request_id} if request_id else None,
    )


app = create_app()
