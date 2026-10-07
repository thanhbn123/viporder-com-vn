"""SQLAlchemy 2.x models for the lead store.

The lead table is the business record of record: a registration that reaches us
is written down *before* the upstream provider is contacted, so a provider
outage can never make a lead disappear (hard constraint: never lose a lead).

Nothing sensitive is stored. The registration password is forwarded to the
provider and then dropped — there is deliberately no column for it, no shadow
column, and no "recent payload" blob.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-aware UTC now. Audit timestamps must never be naive."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class LeadType(StrEnum):
    REGISTER_LEAD = "REGISTER_LEAD"
    QUOTE_LEAD = "QUOTE_LEAD"


class RegistrationStatus(StrEnum):
    PENDING = "PENDING"
    REGISTERED = "REGISTERED"
    FAILED = "FAILED"


class ServiceInterest(StrEnum):
    TRANSPORT = "transport"
    OFFICIAL_IMPORT = "official_import"
    CUSTOMS = "customs"
    ORDER = "order"


# Business rule, enforced by the database rather than by application code:
# at most one REGISTERED registration lead per phone number.
#
# Partial indexes are supported by both SQLite and PostgreSQL, so the same rule
# holds in dev and in production. PENDING/FAILED rows are deliberately outside
# the index: a customer whose first attempt failed must be able to try again.
# A "live" lead for a phone is one that is mid-attempt OR already registered.
# At most one may exist, and that single rule is what both guarantees below need.
#
# WHY ONE INDEX AND NOT TWO. The first version had two: one for REGISTERED rows
# and one for in-flight rows. That left a window CI found and a local run did not
# — request A completes and RELEASES its claim, then request B (whose duplicate
# pre-check ran before A committed) inserts into the now-free claim, calls the
# provider a second time, and only then collides with A's REGISTERED row. Two
# attempts, two provider calls, one registration. Covering both states in one
# predicate refuses B at INSERT no matter how the two orderings interleave.
#
# Predicate is immutable (`IS NOT NULL` / a column comparison), so it is legal on
# both PostgreSQL and SQLite. A time-based predicate would not be.
_LIVE_PHONE_PREDICATE = (
    "lead_type = 'REGISTER_LEAD' "
    "AND (in_flight_at IS NOT NULL OR registration_status = 'REGISTERED')"
)

# One attempt in flight per phone. This is what stops two concurrent
# registrations for the same number from BOTH calling the customer-code
# provider — which could create a duplicate customer upstream that nothing on
# our side points at.
#
# The lead row IS the reservation: it is inserted with `in_flight_at` set, and a
# partial unique index arbitrates. That makes the guarantee a DATABASE invariant
# rather than a check in application code, which is the only kind that holds
# under concurrency. The index is `uq_leads_live_phone`, defined below together
# with the reason it covers registered rows too.
#
# Stale claims (a process killed mid-attempt) are reclaimed in Python rather than
# by the index, because a time-based predicate is not immutable and therefore not
# a legal partial index — see `release_stale_claims`.


class Lead(Base):
    __tablename__ = "leads"

    lead_id: Mapped[str] = mapped_column(String(36), primary_key=True)

    lead_type: Mapped[LeadType] = mapped_column(
        SAEnum(LeadType, name="lead_type", native_enum=False, length=32),
        nullable=False,
        default=LeadType.REGISTER_LEAD,
        server_default=LeadType.REGISTER_LEAD.value,
    )
    registration_status: Mapped[RegistrationStatus] = mapped_column(
        SAEnum(
            RegistrationStatus,
            name="registration_status",
            native_enum=False,
            length=16,
        ),
        nullable=False,
        default=RegistrationStatus.PENDING,
        server_default=RegistrationStatus.PENDING.value,
    )

    # --- Identity -----------------------------------------------------------
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)  # canonical +84...
    phone_display: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    province: Mapped[str | None] = mapped_column(String(120), nullable=True)
    service_interest: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # --- Attribution (UTM) --------------------------------------------------
    source: Mapped[str | None] = mapped_column(String(300), nullable=True)
    medium: Mapped[str | None] = mapped_column(String(300), nullable=True)
    campaign: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content: Mapped[str | None] = mapped_column(String(300), nullable=True)
    term: Mapped[str | None] = mapped_column(String(300), nullable=True)
    landing_page: Mapped[str | None] = mapped_column(String(300), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # --- Provider outcome ---------------------------------------------------
    external_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_customer_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- Deduplication / tracking ------------------------------------------
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tracking_token: Mapped[str] = mapped_column(String(64), nullable=False)
    #: SHA-256 over the canonical request body. An Idempotency-Key alone is not
    #: a safe replay key: the front end keeps one key for the whole form session
    #: and only clears it after a completed registration, so "same key, edited
    #: phone number" is a reachable path. Without this, the replay would hand the
    #: second customer the first customer's lead and customer code.
    #:
    #: The password is deliberately NOT part of the fingerprint. A hash of a
    #: password is still password-derived material, and storing it would create
    #: an offline-cracking target.
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- Legal / consent ----------------------------------------------------
    #: When the customer agreed to have an account created, and which wording
    #: they agreed to. "Consent is required by the schema" is *enforcement*;
    #: these columns are the *evidence*, which is a different thing.
    #:
    #: Nullable so the column can be added to an existing SQLite database
    #: without inventing a server default; every lead this service creates sets
    #: both, and a test asserts that.
    consent_given_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # --- Stored response ----------------------------------------------------
    #: The exact HTTP status and body this attempt produced, written when the
    #: attempt reaches a *terminal* outcome.
    #:
    #: An idempotency key promises the identical stored response. Rebuilding the
    #: response from the row's current state is not the same thing: a DUPLICATE
    #: outcome stores ``FAILED`` and answers ``409``, and a reconstructed reply
    #: answered ``202`` with the pending wording — a different status code for
    #: the same request, carrying the false promise "we will complete it
    #: shortly" about a lead nothing was going to complete.
    #:
    #: Both stay NULL while the lead is PENDING: PENDING is not terminal, and an
    #: admin retry legitimately changes the outcome, so the next terminal write
    #: replaces whatever is stored here.
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- Phone claim (one attempt in flight per phone) ----------------------
    # Set when the attempt begins and cleared by EVERY `update_status` call,
    # because the claim belongs to the ATTEMPT rather than to the status: an
    # attempt that ends PENDING because the provider was unavailable is over, and
    # holding the claim would block the customer's own retry. Gating the release
    # on "terminal status" was the first version of this and it was wrong.
    # Non-null on at most one row per phone, enforced by the partial unique index
    # declared in __table_args__.
    in_flight_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Retry bookkeeping --------------------------------------------------
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- Audit --------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "lead_type IN ('REGISTER_LEAD', 'QUOTE_LEAD')",
            name="ck_leads_lead_type",
        ),
        CheckConstraint(
            "registration_status IN ('PENDING', 'REGISTERED', 'FAILED')",
            name="ck_leads_registration_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_leads_attempt_count"),
        Index("ix_leads_phone", "phone"),
        Index("ix_leads_registration_status", "registration_status"),
        Index("ix_leads_created_at", "created_at"),
        Index("uq_leads_idempotency_key", "idempotency_key", unique=True),
        Index("uq_leads_tracking_token", "tracking_token", unique=True),
        # The single phone rule: one live lead per phone. See
        # _LIVE_PHONE_PREDICATE for why this is one index and not two.
        Index(
            "uq_leads_live_phone",
            "phone",
            unique=True,
            sqlite_where=text(_LIVE_PHONE_PREDICATE),
            postgresql_where=text(_LIVE_PHONE_PREDICATE),
        ),
    )

    def __repr__(self) -> str:  # noqa: D105 - keeps password/PII out of logs
        return (
            f"<Lead lead_id={self.lead_id!r} status={self.registration_status} "
            f"phone_display={self.phone_display!r}>"
        )


def as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    SQLite does not persist timezone offsets, so a value read back from a
    SQLite database is naive. Every timestamp that leaves this service is
    labelled UTC rather than silently interpreted as local time.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
