"""Health endpoint.

Reports the two things an operator actually needs to know: can we reach the
database, and which registration provider is wired up. It never reports a
credential, a DSN, or a provider base URL.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


def provider_health(mode: str, real_calls_enabled: bool, real_registration_enabled: bool) -> str:
    """Overall provider status: ``ok`` when EITHER capability is live.

    WHY **BOTH** ARGUMENTS. The two capabilities were once one switch; they were
    split (see ``khaibao9610_enable_real_registration`` in ``app/config.py``) so
    that live tracking reads could be enabled WITHOUT arming live registration
    writes. This field, however, kept reading only the READ switch — so the one
    configuration that arms customer creation reported ``disabled`` on the field
    named for exactly that purpose, while a sibling field said ``live``.
    MEASURED before the fix, from ``GET /api/v1/health``:

        mode=http, ENABLE_REAL_CALLS=no, ENABLE_REAL_REGISTRATION=yes
        -> {"mode":"http","status":"disabled",
            "capabilities":{"tracking_reads":"disabled","registration_writes":"live"}}

    A monitor that reads ``status`` — the obvious field, and the one the docs
    point at — got a false all-clear on a site that was creating real customer
    accounts on the provider's production API. Either capability live means the
    provider is live.
    """
    if mode == "mock":
        return "ok"
    return "ok" if (real_calls_enabled or real_registration_enabled) else "disabled"


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
            # Whether staff get a Zalo message per new lead. Never the token.
            "notifications": {"zalo": "on" if settings.zalo_notify_enabled else "off"},
            # Public-site DOMY chat: "on" when both upstream URL and secret are set.
            "chat": {"domy": "on" if settings.domy_chat_enabled else "off"},
            "provider": {
                "mode": mode,
                "status": provider_health(
                    mode,
                    settings.khaibao9610_enable_real_calls,
                    settings.khaibao9610_enable_real_registration,
                ),
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
