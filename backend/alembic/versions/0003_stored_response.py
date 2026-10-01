"""store the terminal response for idempotent replay

Revision ID: 0003_stored_response
Revises: 0002_consent_and_fingerprint
Create Date: 2026-02-14

Adds ``response_status`` (integer) and ``response_body`` (JSON) to ``leads``.

Why: an idempotency key promises the **identical stored response**. The service
used to rebuild the reply from the lead's current state, and that is not the
same thing — a lead whose provider outcome was DUPLICATE is stored ``FAILED``
and the original request answers ``409``, but the rebuilt reply answered ``202``
with the *pending* wording ("we will complete it shortly") about a lead nothing
was ever going to complete. Same key, same body, two different status codes.

With these columns the reply produced by a terminal outcome is written down and
returned verbatim on replay. Both stay NULL while the lead is PENDING: PENDING is
not terminal, and an admin retry legitimately changes the outcome, so the next
terminal write replaces whatever is stored.

Guarded like 0002, so `alembic upgrade head` is a no-op when
``AUTO_CREATE_SCHEMA`` has already produced these columns from the ORM metadata.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_stored_response"
down_revision = "0002_consent_and_fingerprint"
branch_labels = None
depends_on = None

NEW_COLUMNS: dict[str, sa.Column] = {
    "response_status": sa.Column("response_status", sa.Integer(), nullable=True),
    "response_body": sa.Column("response_body", sa.JSON(), nullable=True),
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
