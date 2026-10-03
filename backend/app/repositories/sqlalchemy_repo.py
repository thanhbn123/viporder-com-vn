"""SQLAlchemy implementation of :class:`~app.repositories.base.LeadRepository`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
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


class PhoneBusyError(RuntimeError):
    """Another attempt for this phone is already in flight.

    Raised at INSERT, which is the point of the design: the previous behaviour
    detected the conflict on the UPDATE, by which time **the provider had already
    been called** for both requests, so a duplicate customer could exist upstream
    with nothing here pointing at it. Refusing at INSERT means the second attempt
    never reaches the provider at all.
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

    def release_stale_claims(self, older_than: datetime) -> int:
        """Clear in-flight claims abandoned by a process that died mid-attempt.

        A phone claim is released when an attempt reaches a terminal outcome. If
        the process is killed between the INSERT and that update, the claim would
        otherwise block that phone forever — a worse failure than the duplicate
        call the claim exists to prevent.

        Safe against races: two callers may both reclaim, and the partial unique
        index still arbitrates the subsequent INSERT. This only decides *when* a
        claim is considered abandoned, never who wins.
        """
        result = self.session.execute(
            update(Lead)
            .where(Lead.in_flight_at.is_not(None), Lead.in_flight_at < older_than)
            .values(in_flight_at=None)
            # `fetch`, not the default `evaluate`. Evaluating the WHERE clause
            # in PYTHON requires comparing the stored `in_flight_at` with the
            # cutoff, and SQLite returns it NAIVE while the cutoff is
            # timezone-aware — so it raised "can't compare offset-naive and
            # offset-aware datetimes" the moment any row actually held a claim.
            #
            # It stayed hidden because the reclaim runs BEFORE the insert, when
            # no row holds a claim yet, so there was nothing to compare. The
            # app's own SQLite tests never touched it; a test that created a
            # claim first did.
            #
            # `fetch` still keeps the caller's session coherent — `False` would
            # leave the reclaimed row looking claimed to the very code that just
            # reclaimed it, which is its own trap.
            .execution_options(synchronize_session="fetch")
        )
        self.session.commit()
        return int(result.rowcount or 0)

    def claim_phone(self, lead_id: str) -> None:
        """Take the in-flight claim for an existing lead's phone.

        The operator retry route re-attempts an EXISTING lead rather than
        inserting a new one, so it never went through `create` and therefore
        never took the claim. That left the door the claim exists to close wide
        open on the one path a human drives by hand: an admin retry could reach
        the provider while a customer attempt for the same phone was in flight.

        Raises :class:`PhoneBusyError` when another attempt holds the claim —
        either a different row for the same phone (caught by the partial unique
        index on commit) or THIS row, whose own attempt is still running. The
        second case is invisible to the index, because re-claiming the same row is
        not a new row, so it is checked here.
        """
        lead = self.session.get(Lead, lead_id)
        if lead is None:
            return
        if lead.in_flight_at is not None:
            # This lead's own attempt is still in flight. Re-running it now would
            # be a second provider call for the same registration.
            raise PhoneBusyError(lead.phone)
        lead.in_flight_at = utcnow()
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            if _is_phone_uniqueness(exc):
                raise PhoneBusyError(lead.phone) from exc
            raise

    def create(self, lead: Lead) -> Lead:
        self.session.add(lead)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            # Idempotency first: it is named in the error on both dialects, so it
            # is never ambiguous.
            if lead.idempotency_key and _is_idempotency_conflict(exc):
                raise DuplicateIdempotencyKeyError(lead.idempotency_key) from exc
            # Then phone. Since 0005 there is ONE phone rule —
            # `uq_leads_live_phone` — covering both mid-attempt and registered
            # rows, so a phone-uniqueness failure here can be either case. The
            # service disambiguates by asking whether a REGISTERED lead exists
            # (`find_registered_by_phone`), because "your number is taken" and
            # "someone is registering it right now" are different answers.
            #
            # It is raised as PhoneBusyError either way rather than matched by
            # name, because SQLite reports "UNIQUE constraint failed:
            # leads.phone" without naming the index — matching on the name alone
            # would let this through as a 500 on SQLite while passing on
            # PostgreSQL.
            if lead.in_flight_at is not None and _is_phone_uniqueness(exc):
                raise PhoneBusyError(lead.phone) from exc
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

        # The claim belongs to the ATTEMPT, not to the status, and every
        # update_status call in this service happens at the end of an attempt —
        # so every one of them releases it.
        #
        # Gating this on "status is not PENDING" was wrong, and a test caught it:
        # an attempt that ends PENDING because the provider was unavailable is
        # OVER, and keeping the claim would make the customer's own retry collide
        # with a reservation nobody was holding. That would break the one rule the
        # brief states twice — a lead is never lost to an outage.
        lead.in_flight_at = None

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


def _is_phone_uniqueness(exc: IntegrityError) -> bool:
    """True when a phone column violated a unique constraint, whichever index.

    Deliberately does NOT try to tell the two phone indexes apart from the error
    text, because it cannot: SQLite reports ``UNIQUE constraint failed:
    leads.phone`` for both, and only PostgreSQL names the index. The caller
    disambiguates from context instead — at INSERT the row is PENDING, so only the
    in-flight index can fire; at UPDATE it is the registered index.
    """
    message = str(getattr(exc, "orig", exc)).lower()
    if "idempotency" in message:
        return False
    for name in (
        "uq_leads_live_phone",  # the rule since 0005
        "uq_leads_in_flight_phone",  # 0004, before 0005 replaced it
        "uq_leads_registered_phone",  # 0001..0004
    ):
        if name in message:
            return True
    return "unique" in message and "phone" in message


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit]
