"""Admin routes.

These exist so a lead can never be stranded in ``PENDING``/``FAILED``.

Disabled means *absent*, not *closed*: when ``ADMIN_API_TOKEN`` is unset the
route answers ``404`` like any other unknown path, so an unconfigured deployment
does not even advertise that an admin surface exists. A wrong token also gets
``404`` rather than ``401`` — there is no reason to confirm the route is real.

Token comparison is constant-time, so the response time does not leak the token
prefix.
"""

from __future__ import annotations

import logging
import secrets as py_secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from ..dependencies import get_service, get_settings_dep
from ..errors import NOT_FOUND, ApiError
from ..services.registration import RegistrationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

# An HTTP header name, not a credential.
ADMIN_TOKEN_HEADER = "X-Admin-Token"  # nosec B105


class RetryRequest(BaseModel):
    """Optional body for a retry.

    ``password`` is required in practice (the service never stores it) but the
    field is optional so the caller can discover *why* a retry is refused from
    the response rather than from a validation error.
    """

    model_config = ConfigDict(extra="ignore")

    password: str | None = Field(default=None, min_length=8, max_length=200)


def require_admin(request: Request, settings: Settings = Depends(get_settings_dep)) -> Settings:
    if not settings.admin_enabled:
        raise ApiError(404, NOT_FOUND, "Not found.")
    provided = request.headers.get(ADMIN_TOKEN_HEADER)
    if not provided or not py_secrets.compare_digest(provided, settings.admin_api_token):
        logger.warning("admin route rejected: bad or missing X-Admin-Token")
        raise ApiError(404, NOT_FOUND, "Not found.")
    return settings


@router.post("/registrations/{lead_id}/retry")
def retry_registration(
    lead_id: str,
    request: Request,
    payload: RetryRequest | None = None,
    _settings: Settings = Depends(require_admin),
    service: RegistrationService = Depends(get_service),
) -> JSONResponse:
    """Retry a PENDING/FAILED lead against the provider."""
    body = service.retry(lead_id, password=payload.password if payload else None)
    return JSONResponse(status_code=200, content=body)
