"""one attempt in flight per phone

Revision ID: 0004_phone_claim
Revises: 0003_stored_response
Create Date: 2026-02-15

Adds ``in_flight_at`` to ``leads`` plus a partial unique index on ``phone``
where it is not null.

Why: the previous revision fixed the *response* a losing concurrent request
receives, but not the fact that **both requests had already called the provider**
by the time the conflict was detected. Two simultaneous registrations for the
same phone could therefore create two customers upstream, with only one of them
recorded here — a duplicate the customer would discover later, and that nothing
on our side would explain.

A check in application code cannot close that: both requests read "no registered
lead for this phone" before either writes. The guarantee has to be a database
invariant.

**The lead row is the reservation.** It is inserted with ``in_flight_at`` set,
and this index admits at most one such row per phone, so the second concurrent
attempt is refused at INSERT — *before* the provider is called. When the attempt
reaches a terminal outcome the timestamp is cleared, which is also what
distinguishes "in flight" from "PENDING": a PENDING row left behind by a provider
outage is not in flight and must remain retryable.

``IS NOT NULL`` is immutable, so it is a legal partial-index predicate on both
PostgreSQL and SQLite. A time-based predicate would not be, which is why stale
claims (a process killed mid-attempt) are reclaimed in Python by
``LeadRepository.release_stale_claims`` before each insert.

Guarded like 0002/0003, so `alembic upgrade head` is a no-op when
``AUTO_CREATE_SCHEMA`` has already produced this column from the ORM metadata.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_phone_claim"
down_revision = "0003_stored_response"
branch_labels = None
depends_on = None

COLUMN = sa.Column("in_flight_at", sa.DateTime(timezone=True), nullable=True)
INDEX = "uq_leads_in_flight_phone"
PREDICATE = "in_flight_at IS NOT NULL"


def _inspect_state() -> tuple[set[str], set[str]] | None:
    if op.get_context().as_sql:
        return None
    inspector = sa.inspect(op.get_bind())
    if "leads" not in inspector.get_table_names():
        raise RuntimeError(
            f"Revision {revision} requires the 'leads' table created by "
            "0001_create_leads, and it is not present."
        )
    columns = {c["name"] for c in inspector.get_columns("leads")}
    indexes = {i["name"] for i in inspector.get_indexes("leads")}
    return columns, indexes


def upgrade() -> None:
    state = _inspect_state()

    if state is None:  # offline (--sql) mode: render unconditionally
        op.add_column("leads", COLUMN)
        op.create_index(
            INDEX,
            "leads",
            ["phone"],
            unique=True,
            sqlite_where=sa.text(PREDICATE),
            postgresql_where=sa.text(PREDICATE),
        )
        return

    columns, indexes = state
    if "in_flight_at" not in columns:
        op.add_column("leads", COLUMN)
    if INDEX not in indexes:
        op.create_index(
            INDEX,
            "leads",
            ["phone"],
            unique=True,
            sqlite_where=sa.text(PREDICATE),
            postgresql_where=sa.text(PREDICATE),
        )


def downgrade() -> None:
    state = _inspect_state()
    if state is None:
        op.drop_index(INDEX, table_name="leads")
        op.drop_column("leads", "in_flight_at")
        return
    columns, indexes = state
    if INDEX in indexes:
        op.drop_index(INDEX, table_name="leads")
    if "in_flight_at" in columns:
        op.drop_column("leads", "in_flight_at")
