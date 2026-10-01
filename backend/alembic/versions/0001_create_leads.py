"""create leads table and indexes

Revision ID: 0001_create_leads
Revises:
Create Date: 2026-02-14

Creates the ``leads`` table exactly as ``app.models.Lead`` describes it, so the
migrated schema and the ORM metadata stay in step. ``tests/test_alembic.py``
asserts that parity rather than trusting this comment.

Note on the early return: ``AUTO_CREATE_SCHEMA`` lets the application create the
tables itself (the default for a bare ``uvicorn app.main:app`` in development).
When that has already happened, running ``alembic upgrade head`` afterwards must
not explode with "table leads already exists" — the schema is already correct,
so the migration is a no-op. Real deployments set ``AUTO_CREATE_SCHEMA=no`` and
let Alembic own the schema.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_create_leads"
down_revision = None
branch_labels = None
depends_on = None

REGISTERED_PHONE_PREDICATE = "lead_type = 'REGISTER_LEAD' AND registration_status = 'REGISTERED'"


def _leads_table_exists() -> bool:
    """Whether the schema is already materialised.

    In ``--sql`` (offline) mode there is no connection to inspect, and the
    rendered SQL must still be produced, so the guard is skipped there.
    """
    if op.get_context().as_sql:
        return False
    return "leads" in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _leads_table_exists():
        return

    op.create_table(
        "leads",
        sa.Column("lead_id", sa.String(length=36), nullable=False),
        sa.Column(
            "lead_type",
            sa.Enum(
                "REGISTER_LEAD",
                "QUOTE_LEAD",
                name="lead_type",
                native_enum=False,
                length=32,
            ),
            server_default="REGISTER_LEAD",
            nullable=False,
        ),
        sa.Column(
            "registration_status",
            sa.Enum(
                "PENDING",
                "REGISTERED",
                "FAILED",
                name="registration_status",
                native_enum=False,
                length=16,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("phone_display", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("province", sa.String(length=120), nullable=True),
        sa.Column("service_interest", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=300), nullable=True),
        sa.Column("medium", sa.String(length=300), nullable=True),
        sa.Column("campaign", sa.String(length=300), nullable=True),
        sa.Column("content", sa.String(length=300), nullable=True),
        sa.Column("term", sa.String(length=300), nullable=True),
        sa.Column("landing_page", sa.String(length=300), nullable=True),
        sa.Column("referrer", sa.String(length=300), nullable=True),
        sa.Column("external_customer_id", sa.String(length=64), nullable=True),
        sa.Column("external_customer_code", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("tracking_token", sa.String(length=64), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lead_type IN ('REGISTER_LEAD', 'QUOTE_LEAD')",
            name="ck_leads_lead_type",
        ),
        sa.CheckConstraint(
            "registration_status IN ('PENDING', 'REGISTERED', 'FAILED')",
            name="ck_leads_registration_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_leads_attempt_count"),
        sa.PrimaryKeyConstraint("lead_id", name="pk_leads"),
    )

    op.create_index("ix_leads_phone", "leads", ["phone"], unique=False)
    op.create_index("ix_leads_registration_status", "leads", ["registration_status"], unique=False)
    op.create_index("ix_leads_created_at", "leads", ["created_at"], unique=False)
    op.create_index("uq_leads_idempotency_key", "leads", ["idempotency_key"], unique=True)
    op.create_index("uq_leads_tracking_token", "leads", ["tracking_token"], unique=True)
    op.create_index(
        "uq_leads_registered_phone",
        "leads",
        ["phone"],
        unique=True,
        sqlite_where=sa.text(REGISTERED_PHONE_PREDICATE),
        postgresql_where=sa.text(REGISTERED_PHONE_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("uq_leads_registered_phone", table_name="leads")
    op.drop_index("uq_leads_tracking_token", table_name="leads")
    op.drop_index("uq_leads_idempotency_key", table_name="leads")
    op.drop_index("ix_leads_created_at", table_name="leads")
    op.drop_index("ix_leads_registration_status", table_name="leads")
    op.drop_index("ix_leads_phone", table_name="leads")
    op.drop_table("leads")
