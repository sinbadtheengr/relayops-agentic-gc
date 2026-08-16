"""SQLAlchemy models. Every Track-2 table is tenant-scoped by `clinic_id`.

THE HARD RULE, inherited from relayops-prod and non-negotiable here:
**Track 1 (prospect businesses we sell to) and Track 2 (a signed clinic's
customers — consumer PII) never join.** This repo contains Track 2 only. No
model here carries a `prospect_id`, and no query in this repo reads a
prospects table. If a future feature seems to need the join, it is the
feature that is wrong.

One deliberate exception to tenant scoping: `opt_outs` has **no** `clinic_id`.
See its docstring — that absence is the feature.

See CLAUDE.md F-2.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# --- Tenant registry ------------------------------------------------------


class Clinic(Base):
    """A signed clinic. The tenant boundary for everything in Track 2.

    `get_clinic()` in repo.py refuses to create on a miss: a typo must not
    silently split one clinic's data across two tenants.
    """

    __tablename__ = "clinics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --- Client data (consumer PII) ------------------------------------------


class Client(Base):
    """One lapsed client of one clinic.

    `client_key` is the E.164 phone — the natural key a clinic export can be
    matched on. Unique *per clinic*, never globally: two clinics sharing a
    customer are two independent relationships, and collapsing them was a real
    bug in relayops-prod (one clinic's outreach put the other's same-phone
    customer into cooldown, breaking that campaign and leaking that the two
    clinics share a client).

    `visit_count` and `lifetime_spend_cents` are nullable ON PURPOSE. The
    importer writes None, never 0, when a column is unreadable — a blanked
    spend would make a VIP look ordinary. `last_visit` is NOT NULL because a
    row without one is skipped at import rather than defaulted.
    """

    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("clinic_id", "client_key", name="uq_clients_clinic_client"),
        Index("ix_clients_clinic", "clinic_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False
    )
    client_key: Mapped[str] = mapped_column(String(20), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_visit: Mapped[date] = mapped_column(Date, nullable=False)
    visit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lifetime_spend_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_service: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Untrusted free text from the clinic's export. Reaches a prompt, so it is
    # screened by Model Armor before any model call (F-9).
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --- The evidence trail ---------------------------------------------------


class AgentDecision(Base):
    """Every model call, and every rule-gated non-call, lands here.

    Written BEFORE its output is allowed to affect anything else. An unlogged
    decision must never reach a clinic (F-10).

    Rule-gated rows carry `decided_by='rule'`, `model=''`, `tokens=0` and a
    `gate_reason`. Those rows are the record of clients the system
    deliberately did NOT contact — the half of the audit trail a compliance
    review actually asks for.
    """

    __tablename__ = "agent_decisions"
    __table_args__ = (
        CheckConstraint("decided_by IN ('rule','model')", name="ck_agent_decisions_decided_by"),
        Index("ix_agent_decisions_clinic_ts", "clinic_id", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    agent_name: Mapped[str] = mapped_column(String(50), nullable=False)
    clinic_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False
    )
    client_key: Mapped[str | None] = mapped_column(String(20), nullable=True)
    input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decided_by: Mapped[str] = mapped_column(String(10), nullable=False, default="model")
    gate_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)


class ClientDecision(Base):
    """The targeting decision for one client in one run.

    FK to the `agent_decisions` row that produced it, so the dashboard can
    always answer "why did it say that?" with a row rather than a shrug.
    """

    __tablename__ = "client_decisions"
    __table_args__ = (
        UniqueConstraint("clinic_id", "client_key", "run_id", name="uq_client_decisions_run"),
        CheckConstraint("decided_by IN ('rule','model')", name="ck_client_decisions_decided_by"),
        CheckConstraint(
            "priority_tier IS NULL OR priority_tier IN ('A','B','C')",
            name="ck_client_decisions_tier",
        ),
        Index("ix_client_decisions_clinic", "clinic_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False
    )
    client_key: Mapped[str] = mapped_column(String(20), nullable=False)
    run_id: Mapped[str] = mapped_column(String(40), nullable=False)
    target: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Null when a gate stopped the client before the model ran.
    priority_tier: Mapped[str | None] = mapped_column(String(1), nullable=True)
    suggested_offer: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decided_by: Mapped[str] = mapped_column(String(10), nullable=False)
    gate_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    agent_decision_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agent_decisions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --- Drafts (never sent by this system) -----------------------------------


class OutreachDraft(Base):
    """One channel's draft for one client. `status` is the human gate.

    A re-run updates a `draft` row in place and NEVER touches an `approved`,
    `rejected` or `sent` one — a redelivered Pub/Sub message must not produce
    a second draft, and must not silently revise copy a human already
    approved.
    """

    __tablename__ = "outreach_drafts"
    __table_args__ = (
        UniqueConstraint(
            "clinic_id", "client_key", "channel", name="uq_outreach_drafts_clinic_client_channel"
        ),
        CheckConstraint("channel IN ('sms','email')", name="ck_outreach_drafts_channel"),
        CheckConstraint(
            "status IN ('draft','approved','rejected','sent')", name="ck_outreach_drafts_status"
        ),
        Index("ix_outreach_drafts_clinic_status", "clinic_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False
    )
    client_key: Mapped[str] = mapped_column(String(20), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)  # email only
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    # True when a CASL/copy guard flagged it. Rendered as a badge, not just a
    # text prefix, so a reviewer cannot miss it.
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    agent_decision_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agent_decisions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# --- Consent and contact history -----------------------------------------


class ContactLog(Base):
    """Who was contacted, when. Drives the cooldown gate.

    Scoped PER CLINIC, because a cooldown exists so that ONE sender does not
    over-message someone. Clinic A contacting a shared client must not put
    clinic B into cooldown.
    """

    __tablename__ = "contact_log"
    __table_args__ = (Index("ix_contact_log_clinic_client", "clinic_id", "client_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False
    )
    client_key: Mapped[str] = mapped_column(String(20), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    contacted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class OptOut(Base):
    """Permanent do-not-contact. GLOBAL — note the absent clinic_id.

    That absence is the feature and must survive every future migration.
    Scoping opt-outs per clinic would permit contacting someone who opted out
    elsewhere. Under-suppressing is the compliance risk; over-suppressing only
    costs a lead.
    """

    __tablename__ = "opt_outs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_key: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --- Outcomes (billing evidence) -----------------------------------------


class OutreachOutcome(Base):
    """Append-only event log. NOT a status column.

    A client can book, no-show, then rebook and attend, and a billing dispute
    turns on exactly that history. Attribution is recomputed from these rows,
    never stored (F-11), so nothing here is ever UPDATEd or DELETEd.
    """

    __tablename__ = "outreach_outcomes"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('booked','no_show','showed')", name="ck_outreach_outcomes_outcome"
        ),
        Index("ix_outreach_outcomes_clinic_client", "clinic_id", "client_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False
    )
    client_key: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Tables that carry consumer PII scoped to a tenant. The isolation test in
# tests/test_tenant_isolation.py asserts every one of these has a clinic_id
# column, so adding a table here without scoping it fails CI.
TENANT_SCOPED_TABLES = (
    Client.__tablename__,
    AgentDecision.__tablename__,
    ClientDecision.__tablename__,
    OutreachDraft.__tablename__,
    ContactLog.__tablename__,
    OutreachOutcome.__tablename__,
)

# Deliberately global. Kept as an explicit allow-list so the isolation test can
# distinguish "correctly global" from "forgot to scope it".
GLOBAL_TABLES = (OptOut.__tablename__, Clinic.__tablename__)
