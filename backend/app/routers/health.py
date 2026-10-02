"""Health endpoint.

Reports the two things an operator actually needs to know: can we reach the
database, and which registration provider is wired up. It never reports a
credential, a DSN, or a provider base URL.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


def provider_health(mode: str, real_calls_enabled: bool) -> str:
    """Overall provider status. True when EITHER capability is live."""
    if mode == "mock":
        return "ok"
    return "ok" if real_calls_enabled else "disabled"


def capability_status(mode: str, enabled: bool) -> str:
    if mode == "mock":
        return "mock"
    return "live" if enabled else "disabled"


@router.get("/api/v1/health")
def health(request: Request) -> JSONResponse:
    state = request.app.state
    settings = state.settings

    database_ok = state.database.is_healthy()
    mode = settings.khaibao9610_mode

    body = {
        "status": "ok" if database_ok else "error",
        "service": settings.service_name,
        "version": settings.app_version,
        "checks": {
            "database": "ok" if database_ok else "error",
            "provider": {
                "mode": mode,
                "status": provider_health(mode, settings.khaibao9610_enable_real_calls),
                # Reported SEPARATELY. An operator must be able to see whether
                # customer creation is live without reading the env file — the two
                # capabilities used to share one switch, and that is what let a
                # tracking test create an account on the provider's production API.
                "capabilities": {
                    "tracking_reads": capability_status(
                        mode, settings.khaibao9610_enable_real_calls
                    ),
                    "registration_writes": capability_status(
                        mode, settings.khaibao9610_enable_real_registration
                    ),
                },
            },
        },
    }
    return JSONResponse(status_code=200 if database_ok else 503, content=body)
