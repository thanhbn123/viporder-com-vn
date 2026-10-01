"""Registration orchestration.

The ordering here is the whole point of the gate:

1. Validate (in the schema).
2. **Write the lead row first**, status ``PENDING``.
3. Only then call the provider.
4. Record the outcome.

Because step 2 is committed before step 3, there is no provider failure mode —
timeout, 500, connection refused, an adapter that raises, or a process crash
mid-call — that can make a customer's registration disappear. The worst case is
a ``PENDING`` lead with a truthful error code, which is exactly what the retry
route and the tracking token exist for.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime

from pydantic import SecretStr

from ..config import Settings
from ..errors import (
    DUPLICATE_PHONE,
    INVALID_REQUEST,
    NOT_FOUND,
    PROVIDER_INVALID,
    ApiError,
)
from ..logging_filters import register_secret
from ..models import Lead, LeadType, RegistrationStatus, as_utc
from ..phone import phone_display
from ..providers.base import (
    ProviderStatus,
    RegistrationProvider,
    RegistrationRequest,
    RegistrationResult,
)
from ..repositories.base import LeadRepository
from ..repositories.sqlalchemy_repo import DuplicateIdempotencyKeyError
from ..schemas import RegistrationCreate

logger = logging.getLogger(__name__)

MAX_IDEMPOTENCY_KEY_LENGTH = 128

MESSAGE_REGISTERED = "Registration complete. You can sign in to the customer portal."
MESSAGE_PENDING = (
    "We received your registration but could not confirm it yet. "
    "Keep the tracking token to check the result; we will complete it shortly."
)
MESSAGE_ALREADY_REGISTERED = "This registration is already confirmed."
MESSAGE_RETRY_ATTEMPTED = "Retry attempt recorded. Check registration_status for the result."
# Operator-facing wording, not a credential.
MESSAGE_RETRY_NEEDS_PASSWORD = (  # nosec B105
    "A password is required to retry: this service never stores registration "
    'passwords. Resend the request with {"password": "..."} to complete the retry.'
)


class RegistrationService:
    def __init__(
        self,
        *,
        repository: LeadRepository,
        provider: RegistrationProvider,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.settings = settings

    @property
    def login_url(self) -> str:
        """Server-side constant. Never built from request input."""
        return self.settings.login_url

    # -- public API ----------------------------------------------------------

    def register(
        self, payload: RegistrationCreate, *, idempotency_key: str | None = None
    ) -> tuple[int, dict]:
        """Create a lead and attempt registration. Returns ``(status, body)``."""
        key = _normalise_idempotency_key(idempotency_key)

        if key:
            existing = self.repository.get_by_idempotency_key(key)
            if existing is not None:
                logger.info("idempotent replay for lead_id=%s", existing.lead_id)
                return _response_for(existing, self.login_url)

        # Duplicate rule: a phone that already has a REGISTERED lead is a
        # duplicate. A phone whose earlier lead is PENDING/FAILED may try again —
        # that customer is not registered yet, and refusing them would strand
        # them permanently.
        already = self.repository.find_registered_by_phone(payload.phone)
        if already is not None:
            logger.info("duplicate registration attempt for an existing lead")
            raise ApiError(
                409,
                DUPLICATE_PHONE,
                "This phone number is already registered. Please sign in to the customer portal.",
            )

        lead = self._build_lead(payload, key=key)
        try:
            self.repository.create(lead)
        except DuplicateIdempotencyKeyError:
            existing = self.repository.get_by_idempotency_key(key) if key else None
            if existing is not None:
                return _response_for(existing, self.login_url)
            raise

        logger.info(
            "lead created lead_id=%s status=%s provider=%s",
            lead.lead_id,
            lead.registration_status.value,
            self.provider.name,
        )

        return self._attempt(lead, payload)

    def get_status(self, lead_id: str, tracking_token: str | None) -> dict:
        lead = self.repository.get(lead_id)
        # Same 404 for "no such lead" and "wrong token": the response must not
        # confirm that a lead id exists.
        if lead is None or not _token_matches(tracking_token, lead.tracking_token):
            raise ApiError(404, NOT_FOUND, "Not found.")
        return _status_body(lead)

    def retry(self, lead_id: str, *, password: str | None = None) -> dict:
        """Retry a PENDING/FAILED lead against the provider.

        The password is not stored anywhere — that is a hard constraint, not an
        oversight — so an operator retry has to supply it again. Without it we
        cannot honestly talk to the provider, and inventing one would create an
        account the customer cannot sign in to.
        """
        lead = self.repository.get(lead_id)
        if lead is None:
            raise ApiError(404, NOT_FOUND, "Not found.")

        if lead.registration_status is RegistrationStatus.REGISTERED:
            return _retry_body(lead, retried=False, message=MESSAGE_ALREADY_REGISTERED)

        if not password:
            logger.info("retry requested without a password lead_id=%s", lead.lead_id)
            return _retry_body(lead, retried=False, message=MESSAGE_RETRY_NEEDS_PASSWORD)

        payload = RegistrationCreate.model_construct(
            full_name=lead.full_name,
            phone=lead.phone,
            password=SecretStr(password),
            email=lead.email,
            province=lead.province,
            service_interest=lead.service_interest,
            consent=True,
            attribution=None,
        )
        _status, _body = self._attempt(lead, payload, is_retry=True)
        updated = self.repository.get(lead_id) or lead
        return _retry_body(updated, retried=True, message=MESSAGE_RETRY_ATTEMPTED)

    # -- internals -----------------------------------------------------------

    def _build_lead(self, payload: RegistrationCreate, *, key: str | None) -> Lead:
        attribution = payload.attribution
        return Lead(
            lead_id=str(uuid.uuid4()),
            lead_type=LeadType.REGISTER_LEAD,
            registration_status=RegistrationStatus.PENDING,
            full_name=payload.full_name,
            phone=payload.phone,
            phone_display=phone_display(payload.phone),
            email=payload.email,
            province=payload.province,
            service_interest=payload.service_interest,
            source=attribution.utm_source if attribution else None,
            medium=attribution.utm_medium if attribution else None,
            campaign=attribution.utm_campaign if attribution else None,
            content=attribution.utm_content if attribution else None,
            term=attribution.utm_term if attribution else None,
            landing_page=attribution.landing_page if attribution else None,
            referrer=attribution.referrer if attribution else None,
            idempotency_key=key,
            tracking_token=secrets.token_urlsafe(32),
            attempt_count=1,
        )

    def _attempt(
        self, lead: Lead, payload: RegistrationCreate, *, is_retry: bool = False
    ) -> tuple[int, dict]:
        password = payload.password.get_secret_value()
        # Belt and braces: even if some other library logs this exact string
        # during the request, the logging filter knows to scrub it. The redactor
        # keeps a small bounded cache, so it also covers a late error handler.
        register_secret(password)
        request = RegistrationRequest(
            full_name=payload.full_name,
            phone=payload.phone,
            password=password,
            email=payload.email,
            province=payload.province,
            service_interest=payload.service_interest,
        )
        try:
            result = self.provider.register(request)
        except Exception as exc:  # noqa: BLE001 - provider bugs must not 5xx
            logger.exception(
                "provider raised lead_id=%s provider=%s type=%s",
                lead.lead_id,
                self.provider.name,
                type(exc).__name__,
            )
            updated = self.repository.update_status(
                lead.lead_id,
                status=RegistrationStatus.PENDING,
                last_error_code="PROVIDER_ERROR",
                last_error_message=("The registration service reported an internal error."),
                increment_attempt=is_retry,
            )
            return 202, _pending_body(updated or lead, self.login_url)

        return self._apply_result(lead, result, is_retry=is_retry)

    def _apply_result(
        self, lead: Lead, result: RegistrationResult, *, is_retry: bool
    ) -> tuple[int, dict]:
        if result.status is ProviderStatus.SUCCESS:
            updated = self.repository.update_status(
                lead.lead_id,
                status=RegistrationStatus.REGISTERED,
                external_customer_id=result.external_customer_id,
                external_customer_code=result.external_customer_code,
                increment_attempt=is_retry,
            )
            logger.info(
                "lead registered lead_id=%s external_code=%s",
                lead.lead_id,
                result.external_customer_code,
            )
            return 201, _registered_body(updated or lead, self.login_url)

        if result.status is ProviderStatus.DUPLICATE:
            # The lead row is kept and marked FAILED. We deliberately do not
            # return the existing customer code: knowing a phone number must not
            # be enough to learn a customer's code.
            self.repository.update_status(
                lead.lead_id,
                status=RegistrationStatus.FAILED,
                last_error_code="DUPLICATE_PHONE",
                last_error_message=("The provider reports this phone as already registered."),
                increment_attempt=is_retry,
            )
            logger.info("provider reported duplicate lead_id=%s", lead.lead_id)
            raise ApiError(
                409,
                DUPLICATE_PHONE,
                "This phone number is already registered. Please sign in to the customer portal.",
            )

        if result.status is ProviderStatus.INVALID:
            self.repository.update_status(
                lead.lead_id,
                status=RegistrationStatus.FAILED,
                last_error_code="PROVIDER_INVALID",
                last_error_message=(result.message or "The provider rejected these details."),
                increment_attempt=is_retry,
            )
            raise ApiError(
                422,
                PROVIDER_INVALID,
                "The registration service rejected these details. Please check them and try again.",
            )

        # UNAVAILABLE (and anything unexpected): the lead stays PENDING with a
        # truthful error code, and the client gets a usable 202.
        updated = self.repository.update_status(
            lead.lead_id,
            status=RegistrationStatus.PENDING,
            last_error_code=(
                "PROVIDER_TIMEOUT"
                if result.retryable and result.http_status is None
                else "PROVIDER_UNAVAILABLE"
            ),
            last_error_message=(result.message or "The registration service is unavailable."),
            increment_attempt=is_retry,
        )
        logger.warning(
            "provider unavailable lead_id=%s http_status=%s retryable=%s",
            lead.lead_id,
            result.http_status,
            result.retryable,
        )
        return 202, _pending_body(updated or lead, self.login_url)


# --- body builders ----------------------------------------------------------


def _registered_body(lead: Lead, login_url: str) -> dict:
    return {
        "lead_id": lead.lead_id,
        "registration_status": lead.registration_status.value,
        "external_customer_id": lead.external_customer_id,
        "external_customer_code": lead.external_customer_code,
        "message": MESSAGE_REGISTERED,
        "login_url": login_url,
    }


def _pending_body(lead: Lead, login_url: str) -> dict:
    return {
        "lead_id": lead.lead_id,
        "registration_status": lead.registration_status.value,
        "external_customer_id": lead.external_customer_id,
        "external_customer_code": lead.external_customer_code,
        "tracking_token": lead.tracking_token,
        "message": MESSAGE_PENDING,
        "login_url": login_url,
    }


def _retry_body(lead: Lead, *, retried: bool, message: str) -> dict:
    return {
        "lead_id": lead.lead_id,
        "registration_status": lead.registration_status.value,
        "external_customer_code": lead.external_customer_code,
        "attempt_count": lead.attempt_count,
        "retried": retried,
        "message": message,
    }


def _response_for(lead: Lead, login_url: str) -> tuple[int, dict]:
    """Rebuild the response for an already-seen idempotency key."""
    if lead.registration_status is RegistrationStatus.REGISTERED:
        return 201, _registered_body(lead, login_url)
    return 202, _pending_body(lead, login_url)


def _status_body(lead: Lead) -> dict:
    return {
        "lead_id": lead.lead_id,
        "registration_status": lead.registration_status.value,
        "external_customer_code": lead.external_customer_code,
        "created_at": _iso(lead.created_at),
        "updated_at": _iso(lead.updated_at),
    }


# --- helpers ----------------------------------------------------------------


def _normalise_idempotency_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ApiError(
            400,
            INVALID_REQUEST,
            f"Idempotency-Key must be at most {MAX_IDEMPOTENCY_KEY_LENGTH} characters.",
        )
    return key


def _token_matches(provided: str | None, stored: str) -> bool:
    if not provided or not stored:
        return False
    return secrets.compare_digest(provided, stored)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat().replace("+00:00", "Z")
