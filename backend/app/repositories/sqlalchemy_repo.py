"""SQLAlchemy implementation of :class:`~app.repositories.base.LeadRepository`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Lead, LeadType, RegistrationStatus, utcnow
from .base import UNSET


class DuplicateIdempotencyKeyError(RuntimeError):
    """Two concurrent requests claimed the same idempotency key."""


class DuplicatePhoneError(RuntimeError):
    """Two concurrent requests tried to register the same phone number.

    The partial unique index permits exactly one REGISTERED lead per phone, so
    when two attempts race, one of them loses at the database. That is the
    correct outcome — but the loser must be told the truth (409), not handed a
    500. This exception is how the repository says "you lost a race" rather than
    "something went wrong".
    """


class SqlAlchemyLeadRepository:
    """Repository backed by a SQLAlchemy session.

    Each call commits. That is intentional for this service: a lead must be on
    disk *before* the provider call, so a crash mid-request still leaves the
    customer recoverable.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def close(self) -> None:
        self.session.close()

    # -- writes --------------------------------------------------------------

    def create(self, lead: Lead) -> Lead:
        self.session.add(lead)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            if lead.idempotency_key and _is_idempotency_conflict(exc):
                raise DuplicateIdempotencyKeyError(lead.idempotency_key) from exc
            raise
        self.session.refresh(lead)
        return lead

    def update_status(
        self,
        lead_id: str,
        *,
        status: RegistrationStatus,
        external_customer_id: str | None = None,
        external_customer_code: str | None = None,
        last_error_code: str | None = None,
        last_error_message: str | None = None,
        increment_attempt: bool = False,
        response_status: int | None | Any = UNSET,
        response_body: dict | None | Any = UNSET,
    ) -> Lead | None:
        lead = self.session.get(Lead, lead_id)
        if lead is None:
            return None

        lead.registration_status = status
        lead.updated_at = utcnow()

        if external_customer_id is not None:
            lead.external_customer_id = external_customer_id
        if external_customer_code is not None:
            lead.external_customer_code = external_customer_code

        # Error fields are cleared on success and set on failure, so the row
        # always describes the *latest* attempt rather than accumulating noise.
        if status is RegistrationStatus.REGISTERED:
            lead.last_error_code = None
            lead.last_error_message = None
        else:
            lead.last_error_code = last_error_code
            lead.last_error_message = _truncate(last_error_message, 500)

        # `UNSET` (omitted) leaves the stored response alone; an explicit None
        # clears it. A later terminal outcome overwrites it, which is what makes
        # an admin retry that finally succeeds replay as a 201.
        if response_status is not UNSET:
            lead.response_status = response_status
        if response_body is not UNSET:
            lead.response_body = response_body

        if increment_attempt:
            lead.attempt_count = (lead.attempt_count or 0) + 1

        try:
            self.session.commit()
        except IntegrityError as exc:
            # The partial unique index rejected a second REGISTERED lead for
            # this phone: another writer won the race. Raise a TYPE so the caller
            # can answer 409 rather than inventing a duplicate customer.
            #
            # This comment previously described behaviour that did not exist —
            # it re-raised the raw IntegrityError, nothing caught it, and the
            # winner's race left the loser with a 500. Concurrency tests in
            # tests/test_concurrency.py now hold it to the promise.
            self.session.rollback()
            if _is_phone_conflict(exc):
                raise DuplicatePhoneError(lead_id) from exc
            raise
        self.session.refresh(lead)
        return lead

    # -- reads ---------------------------------------------------------------

    def get(self, lead_id: str) -> Lead | None:
        return self.session.get(Lead, lead_id)

    def get_by_idempotency_key(self, key: str) -> Lead | None:
        if not key:
            return None
        stmt = select(Lead).where(Lead.idempotency_key == key)
        return self.session.execute(stmt).scalar_one_or_none()

    def find_registered_by_phone(self, phone: str) -> Lead | None:
        stmt = (
            select(Lead)
            .where(
                Lead.phone == phone,
                Lead.lead_type == LeadType.REGISTER_LEAD,
                Lead.registration_status == RegistrationStatus.REGISTERED,
            )
            .order_by(Lead.created_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def list_pending(self, limit: int = 100) -> list[Lead]:
        stmt = (
            select(Lead)
            .where(
                Lead.registration_status.in_(
                    [RegistrationStatus.PENDING, RegistrationStatus.FAILED]
                )
            )
            .order_by(Lead.created_at.asc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())


def _is_idempotency_conflict(exc: IntegrityError) -> bool:
    message = str(getattr(exc, "orig", exc)).lower()
    return "idempotency" in message or "uq_leads_idempotency_key" in message


def _is_phone_conflict(exc: IntegrityError) -> bool:
    """True when the failure is the one-REGISTERED-lead-per-phone rule.

    Matched on the constraint name first, because that is precise, then on the
    PostgreSQL/psycopg wording as a fallback. Driver text is dialect-specific —
    this is the one place in the project that depends on it, and
    tests/test_concurrency.py exercises both supported dialects' behaviour
    through the same path.
    """
    message = str(getattr(exc, "orig", exc)).lower()
    if "uq_leads_registered_phone" in message:
        return True
    return "unique" in message and "phone" in message and "idempotency" not in message


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]
