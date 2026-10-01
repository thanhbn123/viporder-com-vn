"""Public registration routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..dependencies import get_service
from ..schemas import RegistrationCreate
from ..services.registration import RegistrationService

router = APIRouter(prefix="/api/v1/registrations", tags=["registrations"])

IDEMPOTENCY_HEADER = "Idempotency-Key"
# An HTTP header name, not a credential.
TRACKING_TOKEN_HEADER = "X-Tracking-Token"  # nosec B105


@router.post("")
def create_registration(
    request: Request,
    payload: RegistrationCreate,
    service: RegistrationService = Depends(get_service),
) -> JSONResponse:
    """Register a new customer.

    ``201`` when the provider confirmed the account, ``202`` when the lead was
    kept but the provider could not be reached, ``409``/``422`` for duplicates
    and validation problems.
    """
    status_code, body = service.register(
        payload, idempotency_key=request.headers.get(IDEMPOTENCY_HEADER)
    )
    return JSONResponse(status_code=status_code, content=body)


@router.get("/{lead_id}")
def get_registration(
    lead_id: str,
    request: Request,
    service: RegistrationService = Depends(get_service),
) -> JSONResponse:
    """Return the status of a lead, given its tracking token.

    Requires ``X-Tracking-Token``. Without it (or with a wrong one) the answer is
    a flat ``404`` — the route never confirms whether a lead id exists.

    Returns only non-identifying fields: never the password, phone or email.
    """
    body = service.get_status(lead_id, request.headers.get(TRACKING_TOKEN_HEADER))
    return JSONResponse(status_code=200, content=body)
