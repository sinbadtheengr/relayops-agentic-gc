"""Initial multi-tenant schema

Revision ID: 0001
Revises:
Create Date: 2026-08-16

Eight tables. Six are tenant-scoped by clinic_id; `opt_outs` is global on
purpose (see models.OptOut) and `clinics` is the tenant registry itself.

The constraints here are the product, not bookkeeping:
  - UNIQUE (clinic_id, client_key) — two clinics may share a customer
  - opt_outs has NO clinic_id      — under-suppressing is the compliance risk
  - outreach_outcomes is append-only — billing is recomputed from history
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clinics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "clients",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Integer(),
            sa.ForeignKey("clinics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_key", sa.String(20), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        # NOT NULL: a row without a readable last visit is skipped at import
        # rather than defaulted, because a blank date makes everyone look
        # maximally lapsed.
        sa.Column("last_visit", sa.Date(), nullable=False),
        # Nullable on purpose: the importer writes None, never 0, when a
        # column is unreadable. A blanked spend would make a VIP look ordinary.
        sa.Column("visit_count", sa.Integer(), nullable=True),
        sa.Column("lifetime_spend_cents", sa.BigInteger(), nullable=True),
        sa.Column("last_service", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("clinic_id", "client_key", name="uq_clients_clinic_client"),
    )
    op.create_index("ix_clients_clinic", "clients", ["clinic_id"])

    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("agent_name", sa.String(50), nullable=False),
        sa.Column(
            "clinic_id",
            sa.Integer(),
            sa.ForeignKey("clinics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_key", sa.String(20), nullable=True),
        sa.Column("input", postgresql.JSONB(), nullable=False),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(80), nullable=False, server_default=""),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decided_by", sa.String(10), nullable=False, server_default="model"),
        sa.Column("gate_reason", sa.String(30), nullable=True),
        sa.CheckConstraint("decided_by IN ('rule','model')", name="ck_agent_decisions_decided_by"),
    )
    op.create_index("ix_agent_decisions_clinic_ts", "agent_decisions", ["clinic_id", "ts"])

    op.create_table(
        "client_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Integer(),
            sa.ForeignKey("clinics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_key", sa.String(20), nullable=False),
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("target", sa.Boolean(), nullable=False),
        sa.Column("priority_tier", sa.String(1), nullable=True),
        sa.Column("suggested_offer", sa.Text(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=False, server_default=""),
        sa.Column("decided_by", sa.String(10), nullable=False),
        sa.Column("gate_reason", sa.String(30), nullable=True),
        sa.Column(
            "agent_decision_id",
            sa.BigInteger(),
            sa.ForeignKey("agent_decisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("clinic_id", "client_key", "run_id", name="uq_client_decisions_run"),
        sa.CheckConstraint(
            "decided_by IN ('rule','model')", name="ck_client_decisions_decided_by"
        ),
        sa.CheckConstraint(
            "priority_tier IS NULL OR priority_tier IN ('A','B','C')",
            name="ck_client_decisions_tier",
        ),
    )
    op.create_index("ix_client_decisions_clinic", "client_decisions", ["clinic_id"])

    op.create_table(
        "outreach_drafts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Integer(),
            sa.ForeignKey("clinics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_key", sa.String(20), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "agent_decision_id",
            sa.BigInteger(),
            sa.ForeignKey("agent_decisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # One draft per client per channel: a redelivered Pub/Sub message must
        # not produce a second draft.
        sa.UniqueConstraint(
            "clinic_id", "client_key", "channel", name="uq_outreach_drafts_clinic_client_channel"
        ),
        sa.CheckConstraint("channel IN ('sms','email')", name="ck_outreach_drafts_channel"),
        sa.CheckConstraint(
            "status IN ('draft','approved','rejected','sent')", name="ck_outreach_drafts_status"
        ),
    )
    op.create_index(
        "ix_outreach_drafts_clinic_status", "outreach_drafts", ["clinic_id", "status"]
    )

    op.create_table(
        "contact_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Integer(),
            sa.ForeignKey("clinics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_key", sa.String(20), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column(
            "contacted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_contact_log_clinic_client", "contact_log", ["clinic_id", "client_key"])

    # NO clinic_id. Global by construction, so it cannot be scoped by accident.
    op.create_table(
        "opt_outs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("client_key", sa.String(20), nullable=False, unique=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "outreach_outcomes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Integer(),
            sa.ForeignKey("clinics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_key", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "outcome IN ('booked','no_show','showed')", name="ck_outreach_outcomes_outcome"
        ),
    )
    op.create_index(
        "ix_outreach_outcomes_clinic_client", "outreach_outcomes", ["clinic_id", "client_key"]
    )


def downgrade() -> None:
    op.drop_table("outreach_outcomes")
    op.drop_table("opt_outs")
    op.drop_table("contact_log")
    op.drop_table("outreach_drafts")
    op.drop_table("client_decisions")
    op.drop_table("agent_decisions")
    op.drop_table("clients")
    op.drop_table("clinics")
