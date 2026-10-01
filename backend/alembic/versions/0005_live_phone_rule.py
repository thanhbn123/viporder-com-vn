"""one live lead per phone

Revision ID: 0005_live_phone_rule
Revises: 0004_phone_claim
Create Date: 2026-02-16

Replaces two overlapping partial unique indexes with one:

    0003/0004:  uq_leads_registered_phone  WHERE ...
                   lead_type='REGISTER_LEAD' AND registration_status='REGISTERED'
                uq_leads_in_flight_phone  WHERE in_flight_at IS NOT NULL

    0005:       uq_leads_live_phone        WHERE ...
                   lead_type='REGISTER_LEAD'
                   AND (in_flight_at IS NOT NULL OR registration_status='REGISTERED')

WHY. With two indexes there was a window that CI found and a faster local run did
not, in roughly one attempt in twelve:

  A inserts, takes the claim, calls the provider, completes, and RELEASES the
  claim. B — whose duplicate pre-check ran before A committed — then inserts into
  the now-free claim, calls the provider a SECOND time, and only then collides
  with A's REGISTERED row and answers 409.

Two attempts, two provider calls, one registration. Exactly the duplicate
customer the claim was built to prevent, surviving because the claim protects a
window rather than a rule.

`uq_leads_live_phone` states the rule instead: a phone may have at most one lead
that is either mid-attempt or already registered. B's insert is then refused no
matter how the two orderings interleave, because A's REGISTERED row still lives in
the predicate after A releases its claim.

The predicate is immutable (a null check and a column comparison), so it is legal
on both PostgreSQL and SQLite.

Guarded like 0002..0004, so `alembic upgrade head` is a no-op when
``AUTO_CREATE_SCHEMA`` has already produced this index from the ORM metadata.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_live_phone_rule"
down_revision = "0004_phone_claim"
branch_labels = None
depends_on = None

NEW_INDEX = "uq_leads_live_phone"
OLD_INDEXES = ("uq_leads_registered_phone", "uq_leads_in_flight_phone")

PREDICATE = (
    "lead_type = 'REGISTER_LEAD' "
    "AND (in_flight_at IS NOT NULL OR registration_status = 'REGISTERED')"
)
OLD_REGISTERED_PREDICATE = "lead_type = 'REGISTER_LEAD' AND registration_status = 'REGISTERED'"


def _existing_indexes() -> set[str] | None:
    if op.get_context().as_sql:
        return None
    inspector = sa.inspect(op.get_bind())
    if "leads" not in inspector.get_table_names():
        raise RuntimeError(
            f"Revision {revision} requires the 'leads' table created by "
            "0001_create_leads, and it is not present."
        )
    return {i["name"] for i in inspector.get_indexes("leads")}


def _create(index: str, predicate: str) -> None:
    # Both dialects carry the predicate. Passing only one would make SQLite build
    # a FULL unique index on phone — one lead per phone for all time — silently
    # destroying the PENDING-retry design.
    op.create_index(
        index,
        "leads",
        ["phone"],
        unique=True,
        sqlite_where=sa.text(predicate),
        postgresql_where=sa.text(predicate),
    )


def upgrade() -> None:
    existing = _existing_indexes()

    if existing is None:  # offline (--sql) mode: render unconditionally
        for name in OLD_INDEXES:
            op.drop_index(name, table_name="leads")
        _create(NEW_INDEX, PREDICATE)
        return

    for name in OLD_INDEXES:
        if name in existing:
            op.drop_index(name, table_name="leads")
    if NEW_INDEX not in existing:
        _create(NEW_INDEX, PREDICATE)


def downgrade() -> None:
    existing = _existing_indexes()

    if existing is None:
        op.drop_index(NEW_INDEX, table_name="leads")
        _create("uq_leads_registered_phone", OLD_REGISTERED_PREDICATE)
        _create("uq_leads_in_flight_phone", "in_flight_at IS NOT NULL")
        return

    if NEW_INDEX in existing:
        op.drop_index(NEW_INDEX, table_name="leads")
    if "uq_leads_registered_phone" not in existing:
        _create("uq_leads_registered_phone", OLD_REGISTERED_PREDICATE)
    if "uq_leads_in_flight_phone" not in existing:
        _create("uq_leads_in_flight_phone", "in_flight_at IS NOT NULL")
