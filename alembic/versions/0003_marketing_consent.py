"""Record recipient consent and durable unsubscribe links."""

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    op.execute("""
        CREATE TABLE marketing_consent (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            clinic_id integer NOT NULL REFERENCES clinics(id) ON DELETE CASCADE,
            destination text NOT NULL,
            basis text NOT NULL CHECK (basis IN ('express','purchase','inquiry','publication')),
            event_date date NOT NULL,
            evidence text NOT NULL CHECK (trim(evidence) <> ''),
            source_url text,
            role_relevance text,
            unrestricted boolean NOT NULL DEFAULT false,
            recorded_at timestamptz NOT NULL DEFAULT now(),
            CHECK (basis <> 'publication' OR (unrestricted AND
                coalesce(source_url,'') ~ '^https?://' AND trim(coalesce(role_relevance,'')) <> ''))
        )
    """)
    op.execute("CREATE INDEX marketing_consent_lookup ON marketing_consent(clinic_id,destination)")
    op.execute("ALTER TABLE clinics ADD COLUMN mailing_address text, ADD COLUMN contact_email text")
    op.execute("UPDATE outreach_drafts SET status='draft' WHERE status='approved'")


def downgrade():
    op.execute("ALTER TABLE clinics DROP COLUMN mailing_address, DROP COLUMN contact_email")
    op.drop_table("marketing_consent")
