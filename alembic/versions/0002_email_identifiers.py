"""Email identifiers: clients.email, and email opt-outs

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-16

Two gaps found while porting the consent gates (F-4):

1. `outreach_drafts` accepts an 'email' channel but `clients` had nowhere to
   put an address, so every email draft would have been undeliverable.

2. `opt_outs.client_key` was NOT NULL, so an email unsubscribe could not be
   recorded at all. Under CASL the unsubscribe mechanism must actually work;
   silently discarding one is the failure mode that carries real liability.
   The column becomes nullable and gains a sibling `email`, with a CHECK that
   at least one identifier is present and partial unique indexes so
   uniqueness applies per identifier that exists.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("email", sa.String(320), nullable=True))

    op.add_column("opt_outs", sa.Column("email", sa.String(320), nullable=True))
    op.alter_column("opt_outs", "client_key", existing_type=sa.String(20), nullable=True)

    # The plain UNIQUE from 0001 cannot express "unique when present".
    op.drop_constraint("opt_outs_client_key_key", "opt_outs", type_="unique")

    op.create_index(
        "uq_opt_outs_client_key",
        "opt_outs",
        ["client_key"],
        unique=True,
        postgresql_where=sa.text("client_key IS NOT NULL"),
    )
    op.create_index(
        "uq_opt_outs_email",
        "opt_outs",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_opt_outs_identifier", "opt_outs", "client_key IS NOT NULL OR email IS NOT NULL"
    )


def downgrade() -> None:
    # Rows carrying only an email cannot survive a return to a NOT NULL phone.
    # Deleting suppression records to satisfy a schema rollback would re-open
    # contact to people who opted out, so this refuses instead.
    bind = op.get_bind()
    orphans = bind.execute(
        sa.text("SELECT count(*) FROM opt_outs WHERE client_key IS NULL")
    ).scalar()
    if orphans:
        raise RuntimeError(
            f"{orphans} email-only opt-out(s) would be lost by this downgrade. "
            "Migrate them to a phone identifier first; never drop suppression records."
        )

    op.drop_constraint("ck_opt_outs_identifier", "opt_outs", type_="check")
    op.drop_index("uq_opt_outs_email", table_name="opt_outs")
    op.drop_index("uq_opt_outs_client_key", table_name="opt_outs")
    op.create_unique_constraint("opt_outs_client_key_key", "opt_outs", ["client_key"])
    op.alter_column("opt_outs", "client_key", existing_type=sa.String(20), nullable=False)
    op.drop_column("opt_outs", "email")
    op.drop_column("clients", "email")
