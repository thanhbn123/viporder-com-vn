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

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta

from pydantic import SecretStr

from ..config import CONSENT_VERSION, Settings
from ..errors import (
    DUPLICATE_PHONE,
    IDEMPOTENCY_KEY_REUSED,
    INVALID_REQUEST,
    NOT_FOUND,
    PROVIDER_INVALID,
    REGISTRATION_FAILED,
    REGISTRATION_IN_PROGRESS,
    ApiError,
)
from ..logging_filters import register_secret
from ..models import Lead, LeadType, RegistrationStatus, as_utc, utcnow
from ..phone import phone_display
from ..providers.base import (
    ProviderStatus,
    RegistrationProvider,
    RegistrationRequest,
    RegistrationResult,
)
from ..repositories.base import LeadRepository
from ..repositories.sqlalchemy_repo import (
    DuplicateIdempotencyKeyError,
    DuplicatePhoneError,
    PhoneBusyError,
)
from ..schemas import RegistrationCreate

logger = logging.getLogger(__name__)

MAX_IDEMPOTENCY_KEY_LENGTH = 128

MESSAGE_REGISTERED = "Registration complete. You can sign in to the customer portal."
MESSAGE_PENDING = (
    "We received your registration but could not confirm it yet. "
    "Keep the tracking token to check the result; we will complete it shortly."
)
MESSAGE_ALREADY_REGISTERED = "This registration is already confirmed."
MESSAGE_DUPLICATE_PHONE = (
    "This phone number is already registered. Please sign in to the customer portal."
)
MESSAGE_PROVIDER_INVALID = (
    "The registration service rejected these details. Please check them and try again."
)
MESSAGE_REGISTRATION_FAILED = "This registration did not complete. Please start a new registration."
MESSAGE_RETRY_ATTEMPTED = "Retry attempt recorded. Check registration_status for the result."
MESSAGE_RETRY_IN_PROGRESS = (
    "Another attempt for this phone is already in flight. Try again once it finishes."
)
# Operator-facing wording, not a credential.
MESSAGE_RETRY_NEEDS_PASSWORD = (  # nosec B105
    "A password is required to retry: this service never stores registration "
    'passwords. Resend the request with {"password": "..."} to complete the retry.'
)


class ProviderContractViolation(TypeError):
    """The provider returned something that is not a ``RegistrationResult``."""


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
        fingerprint = fingerprint_payload(payload)

        if key:
            existing = self.repository.get_by_idempotency_key(key)
            if existing is not None:
                return self._replay(existing, fingerprint)

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

        # Reclaim claims abandoned by a process that died mid-attempt, so a crash
        # can never block a phone permanently. Safe to run concurrently: the
        # partial unique index still arbitrates the INSERT below.
        reclaimed = self.repository.release_stale_claims(
            utcnow() - timedelta(seconds=self.settings.phone_claim_ttl_seconds)
        )
        if reclaimed:
            logger.warning("reclaimed %d abandoned phone claim(s)", reclaimed)

        lead = self._build_lead(payload, key=key, fingerprint=fingerprint)
        try:
            self.repository.create(lead)
        except PhoneBusyError:
            # Another attempt for this phone is in flight RIGHT NOW, and it has
            # not yet reached the provider. Refusing here is the point: the
            # alternative was to let both call the provider and reconcile later,
            # which risks two customers upstream.
            logger.info("phone already has an attempt in flight; refusing early")
            raise ApiError(
                409,
                REGISTRATION_IN_PROGRESS,
                "A registration for this phone number is already in progress. "
                "Please wait a moment and try again.",
            ) from None
        except DuplicateIdempotencyKeyError:
            # Another writer won the race for this key. Replay only if the body
            # matches; otherwise it is the same misuse, not a retry.
            existing = self.repository.get_by_idempotency_key(key) if key else None
            if existing is not None:
                return self._replay(existing, fingerprint)
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
        # Take the same claim the customer path takes. Without this the operator
        # route was the one door the claim did not cover, and it is the door a
        # human drives by hand.
        try:
            self.repository.claim_phone(lead.lead_id)
        except PhoneBusyError:
            logger.info("admin retry refused: a phone claim is already held")
            return _retry_body(lead, retried=False, message=MESSAGE_RETRY_IN_PROGRESS)

        _status, _body = self._attempt(lead, payload, is_retry=True)
        updated = self.repository.get(lead_id) or lead
        return _retry_body(updated, retried=True, message=MESSAGE_RETRY_ATTEMPTED)

    # -- internals -----------------------------------------------------------

    def _replay(self, existing: Lead, fingerprint: str) -> tuple[int, dict]:
        """Answer a repeated Idempotency-Key.

        A key is only a *request* identity if the body matches. The front end
        keeps one key for the whole form session and only clears it after a
        completed registration, so "same key, corrected phone number" is a real
        path — and replaying blindly there would show customer B customer A's
        lead id and customer code.

        Fails closed: a lead with no stored fingerprint cannot be proven to
        match, so it is refused rather than replayed.
        """
        if existing.request_fingerprint != fingerprint:
            logger.warning(
                "idempotency key reused with a different body lead_id=%s",
                existing.lead_id,
            )
            raise ApiError(
                409,
                IDEMPOTENCY_KEY_REUSED,
                "This Idempotency-Key was already used for a different "
                "registration. Start a new registration.",
            )
        logger.info("idempotent replay for lead_id=%s", existing.lead_id)
        return _response_for(existing, self.login_url)

    def _build_lead(
        self, payload: RegistrationCreate, *, key: str | None, fingerprint: str
    ) -> Lead:
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
            request_fingerprint=fingerprint,
            consent_given_at=utcnow(),
            consent_version=CONSENT_VERSION,
            tracking_token=secrets.token_urlsafe(32),
            attempt_count=1,
            # The row IS the reservation for this phone: a partial unique
            # index admits at most one row with this set, so a second
            # concurrent attempt is refused at INSERT — before the provider
            # is reached. Cleared when the attempt reaches a terminal outcome.
            in_flight_at=utcnow(),
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
            # Validate the *shape* inside the guard, not outside it. A provider
            # that returns something unexpected is an adapter bug, and an
            # adapter bug must surface as a retained lead with a 202 — not as a
            # 500 the customer cannot act on.
            if not isinstance(result, RegistrationResult):
                raise ProviderContractViolation(
                    f"provider returned {type(result).__name__}, expected RegistrationResult"
                )
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
                last_error_code=(
                    "PROVIDER_CONTRACT_ERROR"
                    if isinstance(exc, ProviderContractViolation)
                    else "PROVIDER_ERROR"
                ),
                last_error_message=("The registration service reported an internal error."),
                increment_attempt=is_retry,
            )
            return 202, _pending_body(updated or lead, self.login_url)

        return self._apply_result(lead, result, is_retry=is_retry)

    def _apply_result(
        self, lead: Lead, result: RegistrationResult, *, is_retry: bool
    ) -> tuple[int, dict]:
        if result.status is ProviderStatus.SUCCESS:
            # Built from the values the update is about to write, then stored and
            # returned as the same object — so a replay cannot drift from the
            # original reply.
            body = registered_body(
                lead.lead_id,
                result.external_customer_id,
                result.external_customer_code,
                self.login_url,
            )
            try:
                self.repository.update_status(
                    lead.lead_id,
                    status=RegistrationStatus.REGISTERED,
                    external_customer_id=result.external_customer_id,
                    external_customer_code=result.external_customer_code,
                    increment_attempt=is_retry,
                    response_status=201,
                    response_body=body,
                )
            except DuplicatePhoneError:
                # Another request registered this phone while the provider call
                # for this one was in flight. The partial unique index is the
                # arbiter and it picked the other writer, which is correct — but
                # this caller must be told the truth.
                #
                # Leaving it as a 500 was the previous behaviour, and worse than
                # it looked: by this point the PROVIDER HAS ALREADY BEEN CALLED,
                # so the row would have been stranded in PENDING with an
                # upstream customer that nothing recorded. Marking it FAILED is
                # truthful and lets it be retried or reconciled.
                self.repository.update_status(
                    lead.lead_id,
                    status=RegistrationStatus.FAILED,
                    last_error_code=DUPLICATE_PHONE,
                    last_error_message=(
                        "Another registration for this phone completed first; "
                        "this attempt lost the race."
                    ),
                    increment_attempt=is_retry,
                )
                logger.warning(
                    "duplicate phone detected at the database, not by the "
                    "pre-check lead_id=%s — concurrent registration",
                    lead.lead_id,
                )
                raise ApiError(
                    409,
                    DUPLICATE_PHONE,
                    "This phone number is already registered. Please sign in to the customer portal.",
                ) from None
            logger.info(
                "lead registered lead_id=%s external_code=%s",
                lead.lead_id,
                result.external_customer_code,
            )
            return 201, body

        if result.status is ProviderStatus.DUPLICATE:
            # The lead row is kept and marked FAILED. We deliberately do not
            # return the existing customer code: knowing a phone number must not
            # be enough to learn a customer's code.
            #
            # The 409 envelope is built once and both stored and raised, so the
            # replay of this request is the same object the original returned.
            envelope = error_envelope(DUPLICATE_PHONE, MESSAGE_DUPLICATE_PHONE)
            self.repository.update_status(
                lead.lead_id,
                status=RegistrationStatus.FAILED,
                last_error_code="DUPLICATE_PHONE",
                last_error_message=("The provider reports this phone as already registered."),
                increment_attempt=is_retry,
                response_status=409,
                response_body=envelope,
            )
            logger.info("provider reported duplicate lead_id=%s", lead.lead_id)
            raise ApiError(409, DUPLICATE_PHONE, MESSAGE_DUPLICATE_PHONE)

        if result.status is ProviderStatus.INVALID:
            envelope = error_envelope(PROVIDER_INVALID, MESSAGE_PROVIDER_INVALID)
            self.repository.update_status(
                lead.lead_id,
                status=RegistrationStatus.FAILED,
                last_error_code="PROVIDER_INVALID",
                last_error_message=(result.message or "The provider rejected these details."),
                increment_attempt=is_retry,
                response_status=422,
                response_body=envelope,
            )
            raise ApiError(422, PROVIDER_INVALID, MESSAGE_PROVIDER_INVALID)

        # UNAVAILABLE (and anything unexpected): the lead stays PENDING with a
        # truthful error code, and the client gets a usable 202.
        #
        # PENDING is not terminal, so any response stored by an earlier terminal
        # outcome is cleared. Keeping it would let a replay report a dead
        # outcome for a lead an admin retry may still complete.
        updated = self.repository.update_status(
            lead.lead_id,
            status=RegistrationStatus.PENDING,
            last_error_code=_unavailable_code(result),
            last_error_message=(result.message or "The registration service is unavailable."),
            increment_attempt=is_retry,
            response_status=None,
            response_body=None,
        )
        logger.warning(
            "provider unavailable lead_id=%s code=%s http_status=%s retryable=%s",
            lead.lead_id,
            _unavailable_code(result),
            result.http_status,
            result.retryable,
        )
        return 202, _pending_body(updated or lead, self.login_url)


# --- body builders ----------------------------------------------------------


def error_envelope(code: str, message: str) -> dict:
    """The public error shape, built once so it can be stored and raised.

    ``ApiError.envelope()`` produces exactly this dict. Keeping one builder means
    the stored body and the raised body cannot drift apart.
    """
    return {"error": {"code": code, "message": message}}


def registered_body(
    lead_id: str,
    external_customer_id: str | None,
    external_customer_code: str | None,
    login_url: str,
) -> dict:
    return {
        "lead_id": lead_id,
        "registration_status": RegistrationStatus.REGISTERED.value,
        "external_customer_id": external_customer_id,
        "external_customer_code": external_customer_code,
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


#: Derives the reply for a FAILED lead that predates the stored-response columns
#: (or was written by something other than this service). Keyed on the recorded
#: cause, which is the only truthful evidence left on the row.
_LEGACY_FAILED_RESPONSES: dict[str, tuple[int, str, str]] = {
    "DUPLICATE_PHONE": (409, DUPLICATE_PHONE, MESSAGE_DUPLICATE_PHONE),
    "PROVIDER_INVALID": (422, PROVIDER_INVALID, MESSAGE_PROVIDER_INVALID),
}


def _response_for(lead: Lead, login_url: str) -> tuple[int, dict]:
    """Return the reply for an already-seen idempotency key.

    Order matters:

    1. ``PENDING`` is answered from *current state*, always. It is not terminal,
       so there is no stored reply to honour and a later admin retry legitimately
       changes it.
    2. A stored response is returned verbatim. This is the case that was broken:
       reconstruction turned a 409 into a 202.
    3. Only for a row written before the stored-response columns existed does
       the reply get derived. The two causes this service can actually produce
       are mapped explicitly; anything else **fails closed** with a generic 409
       rather than guessing a code the client would act on.
    """
    if lead.registration_status is RegistrationStatus.PENDING:
        return 202, _pending_body(lead, login_url)

    if lead.response_status is not None and lead.response_body is not None:
        return lead.response_status, lead.response_body

    logger.warning(
        "no stored response for a terminal lead; deriving lead_id=%s status=%s code=%s",
        lead.lead_id,
        lead.registration_status.value,
        lead.last_error_code,
    )

    if lead.registration_status is RegistrationStatus.REGISTERED:
        return 201, registered_body(
            lead.lead_id,
            lead.external_customer_id,
            lead.external_customer_code,
            login_url,
        )

    known = _LEGACY_FAILED_RESPONSES.get(lead.last_error_code or "")
    if known is not None:
        status_code, code, message = known
        return status_code, error_envelope(code, message)

    # Fail closed: never claim success, never claim pending, never invent a code.
    return 409, error_envelope(REGISTRATION_FAILED, MESSAGE_REGISTRATION_FAILED)


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


def fingerprint_payload(payload: RegistrationCreate) -> str:
    """SHA-256 over the parts of a registration that define *whose* it is.

    Used to bind an ``Idempotency-Key`` to one request body. The password is
    excluded on purpose: a hash of a password is password-derived material, and
    storing it would hand an attacker a cheap offline-cracking target for no
    benefit — the key is already scoped to a single browser session.

    Everything that ends up on the lead (identity, contact, service interest,
    consent and the whole attribution block) is included, because changing any
    of it means the caller is describing a different registration.
    """
    attribution = payload.attribution
    canonical = {
        "full_name": payload.full_name,
        "phone": payload.phone,
        "email": payload.email,
        "province": payload.province,
        "service_interest": payload.service_interest,
        "consent": payload.consent,
        "attribution": {
            "utm_source": attribution.utm_source if attribution else None,
            "utm_medium": attribution.utm_medium if attribution else None,
            "utm_campaign": attribution.utm_campaign if attribution else None,
            "utm_content": attribution.utm_content if attribution else None,
            "utm_term": attribution.utm_term if attribution else None,
            "landing_page": attribution.landing_page if attribution else None,
            "referrer": attribution.referrer if attribution else None,
        },
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _unavailable_code(result: RegistrationResult) -> str:
    """Distinguish a real outage from a slow provider.

    A connection refusal and a read timeout both arrive with ``http_status``
    None, and calling both ``PROVIDER_TIMEOUT`` makes a genuine outage
    undiagnosable from the stored data. The adapter names the condition; the
    fallback only covers an adapter that does not.
    """
    if result.error_code:
        return result.error_code
    if result.retryable and result.http_status is None:
        return "PROVIDER_TIMEOUT"
    return "PROVIDER_UNAVAILABLE"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat().replace("+00:00", "Z")
