"""Campaign-run data access: who is eligible, and idempotent result writes.

Every function here is clinic-scoped except `active_clinics`, which is
explicitly cross-tenant and says so.

The upserts are the important part. Pub/Sub delivers at least once, so every
write in this module must be safe to replay — and must never overwrite work a
human has already acted on.

See CLAUDE.md F-6.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import Client, ClientDecision, Clinic, OutreachDraft
from .repo import unguarded

# A draft a human has touched is frozen. A nightly re-run may refresh copy
# that is still awaiting review; it must never revise, revive, or overwrite
# something already approved, rejected, or sent.
MUTABLE_DRAFT_STATUS = "draft"


def active_clinics(session: Session) -> list[Clinic]:
    """Every active tenant. Deliberately cross-tenant — the publisher's job.

    Reads only the `clinics` registry, which holds no client PII.
    """
    with unguarded():
        return list(session.execute(select(Clinic).where(Clinic.active.is_(True))).scalars())


def clinic_spends(session: Session, *, clinic_id: int) -> list[int | None]:
    """Every known lifetime spend for one clinic, for the VIP percentile.

    Scoped per clinic: a cross-tenant percentile would leak one clinic's price
    band into another's targeting.
    """
    return list(
        session.execute(
            select(Client.lifetime_spend_cents).where(Client.clinic_id == clinic_id)
        ).scalars()
    )


def eligible_clients(
    session: Session, *, clinic_id: int, as_of: date, min_days_lapsed: int = 90, limit: int = 1000
) -> list[Client]:
    """Clients lapsed enough to be worth a run, oldest visit first.

    The 90-day floor matches `core.features.lapse_bucket` returning None below
    it: a client with no bucket has no approved template, so enqueueing them
    would only produce a message the worker must discard.

    Compliance gates are NOT applied here. They run in the worker, per client,
    so that every exclusion produces its own recorded decision row. Filtering
    them out at publish time would be cheaper and would leave no evidence.
    """
    cutoff = date.fromordinal(as_of.toordinal() - min_days_lapsed)
    return list(
        session.execute(
            select(Client)
            .where(Client.clinic_id == clinic_id, Client.last_visit <= cutoff)
            .order_by(Client.last_visit.asc())
            .limit(limit)
        ).scalars()
    )


def get_client(session: Session, *, clinic_id: int, client_key: str) -> Client | None:
    return session.execute(
        select(Client).where(Client.clinic_id == clinic_id, Client.client_key == client_key)
    ).scalar_one_or_none()


def record_client_decision(
    session: Session,
    *,
    clinic_id: int,
    client_key: str,
    run_id: str,
    target: bool,
    decided_by: str,
    reasoning: str = "",
    priority_tier: str | None = None,
    suggested_offer: str | None = None,
    gate_reason: str | None = None,
    agent_decision_id: int | None = None,
) -> None:
    """Upsert this run's decision for this client. Replay-safe.

    Keyed on (clinic_id, client_key, run_id): a redelivered message for the
    same run updates the row rather than adding a second verdict for the same
    client on the same night.
    """
    stmt = pg_insert(ClientDecision).values(
        clinic_id=clinic_id,
        client_key=client_key,
        run_id=run_id,
        target=target,
        priority_tier=priority_tier,
        suggested_offer=suggested_offer,
        reasoning=reasoning,
        decided_by=decided_by,
        gate_reason=gate_reason,
        agent_decision_id=agent_decision_id,
    )
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_client_decisions_run",
            set_={
                "target": stmt.excluded.target,
                "priority_tier": stmt.excluded.priority_tier,
                "suggested_offer": stmt.excluded.suggested_offer,
                "reasoning": stmt.excluded.reasoning,
                "decided_by": stmt.excluded.decided_by,
                "gate_reason": stmt.excluded.gate_reason,
                "agent_decision_id": stmt.excluded.agent_decision_id,
            },
        )
    )


def upsert_draft(
    session: Session,
    *,
    clinic_id: int,
    client_key: str,
    channel: str,
    body: str,
    subject: str | None = None,
    needs_review: bool = False,
    agent_decision_id: int | None = None,
) -> None:
    """Write a draft, replacing an existing one ONLY while it is still a draft.

    The `WHERE status = 'draft'` clause is the guarantee that matters: a
    re-run must never revise copy a human already approved, revive something
    they rejected, or reopen a draft already sent. Without it, a redelivered
    Pub/Sub message could silently change a message the clinic had signed off.
    """
    stmt = pg_insert(OutreachDraft).values(
        clinic_id=clinic_id,
        client_key=client_key,
        channel=channel,
        subject=subject,
        body=body,
        status=MUTABLE_DRAFT_STATUS,
        needs_review=needs_review,
        agent_decision_id=agent_decision_id,
    )
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_outreach_drafts_clinic_client_channel",
            set_={
                "subject": stmt.excluded.subject,
                "body": stmt.excluded.body,
                "needs_review": stmt.excluded.needs_review,
                "agent_decision_id": stmt.excluded.agent_decision_id,
            },
            where=OutreachDraft.status == MUTABLE_DRAFT_STATUS,
        )
    )


def upsert_clients(
    session: Session, *, clinic_id: int, records: list[dict]
) -> tuple[int, int]:
    """Load imported client rows into one clinic. Returns (inserted, updated).

    Replay-safe on `(clinic_id, client_key)`: re-importing a refreshed export
    updates the client in place rather than creating a second row for the same
    person. A clinic re-exports every time they want a new wave, so this path
    runs repeatedly over overlapping data by design.

    Notes are updated too, and remain untrusted — everything downstream
    screens them before they can reach a prompt (F-9).
    """
    if not records:
        return 0, 0

    existing = {
        key
        for (key,) in session.execute(
            select(Client.client_key).where(Client.clinic_id == clinic_id)
        ).all()
    }
    inserted = sum(1 for r in records if r["client_key"] not in existing)

    for record in records:
        stmt = pg_insert(Client).values(clinic_id=clinic_id, **record)
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_clients_clinic_client",
                set_={
                    "first_name": stmt.excluded.first_name,
                    "email": stmt.excluded.email,
                    "last_visit": stmt.excluded.last_visit,
                    "visit_count": stmt.excluded.visit_count,
                    "lifetime_spend_cents": stmt.excluded.lifetime_spend_cents,
                    "last_service": stmt.excluded.last_service,
                    "notes": stmt.excluded.notes,
                },
            )
        )
    return inserted, len(records) - inserted
