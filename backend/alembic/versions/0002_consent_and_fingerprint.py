"""add consent evidence and idempotency fingerprint

Revision ID: 0002_consent_and_fingerprint
Revises: 0001_create_leads
Create Date: 2026-02-14

Adds three columns to ``leads``:

* ``consent_given_at`` / ``consent_version`` — the *evidence* that a customer
  agreed to have an account created, and which wording they agreed to. Consent
  was already enforced by the request schema; enforcement without a record is
  not an audit trail. Personal-data matter, not a nicety.
* ``request_fingerprint`` — a SHA-256 over the canonical request body. It binds
  an ``Idempotency-Key`` to one specific registration, so a replay with a
  different body is refused instead of handing customer B customer A's data.

All three are nullable so the columns can be added to an existing SQLite
database without inventing a server default for rows that predate the column.
Every lead the service creates sets all three; ``tests/test_consent.py`` asserts
that, so "nullable" does not become "usually empty".

Both statements are guarded, so `alembic upgrade head` is a no-op when
``AUTO_CREATE_SCHEMA`` has already produced these columns from the ORM metadata.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_consent_and_fingerprint"
down_revision = "0001_create_leads"
branch_labels = None
depends_on = None

NEW_COLUMNS: dict[str, sa.Column] = {
    "consent_given_at": sa.Column("consent_given_at", sa.DateTime(timezone=True), nullable=True),
    "consent_version": sa.Column("consent_version", sa.String(length=32), nullable=True),
    "request_fingerprint": sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
}


def _existing_columns() -> set[str] | None:
    if op.get_context().as_sql:
        return None
    inspector = sa.inspect(op.get_bind())
    if "leads" not in inspector.get_table_names():
        raise RuntimeError(
            f"Revision {revision} requires the 'leads' table created by "
            "0001_create_leads, and it is not present."
        )
    return {column["name"] for column in inspector.get_columns("leads")}


def upgrade() -> None:
    existing = _existing_columns()

    if existing is not None:
        for name, column in NEW_COLUMNS.items():
            if name not in existing:
                op.add_column("leads", column)
        return

    # Offline (--sql) mode: render the statements unconditionally.
    for column in NEW_COLUMNS.values():
        op.add_column("leads", column)


def downgrade() -> None:
    existing = _existing_columns()
    if existing is None:
        for name in NEW_COLUMNS:
            op.drop_column("leads", name)
        return
    for name in NEW_COLUMNS:
        if name in existing:
            op.drop_column("leads", name)
