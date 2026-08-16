"""Loading the evidence attribution is computed from.

`core.attribution` is pure so the billing rules can be argued with in a test
rather than against a database. This module is the only place that knows
where its inputs live.

See CLAUDE.md F-11.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.attribution import BillingSummary, Contact, Outcome, attribute
from .models import Client, ContactLog, OutreachOutcome


def load_contacts(session: Session, *, clinic_id: int) -> list[Contact]:
    rows = session.execute(
        select(ContactLog.client_key, ContactLog.contacted_at, ContactLog.channel).where(
            ContactLog.clinic_id == clinic_id
        )
    ).all()
    return [Contact(client_key=r.client_key, on=r.contacted_at.date(), channel=r.channel) for r in rows]


def load_outcomes(
    session: Session, *, clinic_id: int, since: date | None = None, until: date | None = None
) -> list[Outcome]:
    """Outcomes joined to client names, for a billing period.

    LEFT-joined on purpose: an outcome logged for a client who has since been
    removed from the export still happened, and dropping it from the invoice
    would quietly change what the clinic owes.
    """
    stmt = (
        select(
            OutreachOutcome.client_key,
            OutreachOutcome.outcome,
            OutreachOutcome.occurred_on,
            Client.first_name,
        )
        .join(
            Client,
            (Client.clinic_id == OutreachOutcome.clinic_id)
            & (Client.client_key == OutreachOutcome.client_key),
            isouter=True,
        )
        .where(OutreachOutcome.clinic_id == clinic_id)
    )
    if since is not None:
        stmt = stmt.where(OutreachOutcome.occurred_on >= since)
    if until is not None:
        stmt = stmt.where(OutreachOutcome.occurred_on <= until)

    return [
        Outcome(
            client_key=r.client_key,
            client_name=r.first_name or r.client_key,
            outcome=r.outcome,
            occurred_on=r.occurred_on,
        )
        for r in session.execute(stmt).all()
    ]


def billing_summary(
    session: Session, *, clinic_id: int, since: date | None = None, until: date | None = None
) -> BillingSummary:
    """What this clinic owes for the period, and the evidence for every line."""
    settings = get_settings()
    return attribute(
        load_outcomes(session, clinic_id=clinic_id, since=since, until=until),
        load_contacts(session, clinic_id=clinic_id),
        window_days=settings.attribution_window_days,
        fee_cents=settings.show_fee_cents,
        cap_cents=settings.fee_cap_cents,
    )


def record_outcome(
    session: Session,
    *,
    clinic_id: int,
    client_key: str,
    outcome: str,
    occurred_on: date,
) -> OutreachOutcome:
    """Append one appointment result. Append-only: never updated, never deleted.

    A client can book, no-show, then rebook and attend, and a billing dispute
    turns on exactly that history.
    """
    row = OutreachOutcome(
        clinic_id=clinic_id, client_key=client_key, outcome=outcome, occurred_on=occurred_on
    )
    session.add(row)
    return row
