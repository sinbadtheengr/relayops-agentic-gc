"""Queries for the approval surface.

Every function takes `clinic_id` explicitly — including the ones that look up
a single draft by id. That is the tenant guard shaping the API for the
better: a `get_draft(draft_id)` would have to run unguarded, and an operator
following a stale link would silently read another clinic's client.

See CLAUDE.md F-8.
"""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import AgentDecision, ClientDecision, OutreachDraft

DRAFT_STATUSES = ("draft", "approved", "rejected", "sent")


def draft_counts(session: Session, *, clinic_id: int) -> dict[str, int]:
    """Counts per status, with every status present even at zero.

    A missing tab reads as "no such thing"; a zero reads as "nothing here
    yet", which is the true statement.
    """
    rows = session.execute(
        select(OutreachDraft.status, func.count())
        .where(OutreachDraft.clinic_id == clinic_id)
        .group_by(OutreachDraft.status)
    ).all()
    counts = dict.fromkeys(DRAFT_STATUSES, 0)
    for status, count in rows:
        counts[status] = count
    return counts


def drafts_for_clinic(
    session: Session, *, clinic_id: int, status: str = "draft", limit: int = 200
) -> list[OutreachDraft]:
    """One clinic's drafts in a status. Needs-review first — those are the
    ones a reviewer must not skim past."""
    return list(
        session.execute(
            select(OutreachDraft)
            .where(OutreachDraft.clinic_id == clinic_id, OutreachDraft.status == status)
            .order_by(OutreachDraft.needs_review.desc(), OutreachDraft.client_key)
            .limit(limit)
        ).scalars()
    )


def get_draft(session: Session, *, clinic_id: int, draft_id: int) -> OutreachDraft | None:
    return session.execute(
        select(OutreachDraft).where(
            OutreachDraft.clinic_id == clinic_id, OutreachDraft.id == draft_id
        )
    ).scalar_one_or_none()


def decision_for_draft(
    session: Session, *, clinic_id: int, draft: OutreachDraft
) -> AgentDecision | None:
    """The model call that produced this draft.

    The answer to "why did it say that to my client?" is a row, not a shrug.
    """
    if draft.agent_decision_id is None:
        return None
    return session.execute(
        select(AgentDecision).where(
            AgentDecision.clinic_id == clinic_id, AgentDecision.id == draft.agent_decision_id
        )
    ).scalar_one_or_none()


def skipped_clients(
    session: Session, *, clinic_id: int, limit: int = 500
) -> list[ClientDecision]:
    """Clients the gates excluded, with the reason each was excluded.

    The drafts queue only shows who WAS contacted. The compliance question is
    who was not, and why — and this view is also the fastest demonstration
    that rules, not the model, decide contact eligibility.
    """
    return list(
        session.execute(
            select(ClientDecision)
            .where(
                ClientDecision.clinic_id == clinic_id,
                ClientDecision.decided_by == "rule",
            )
            .order_by(ClientDecision.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def set_draft_status(
    session: Session, *, clinic_id: int, draft_id: int, new_status: str
) -> int:
    """Scoped status change. Returns rows affected (0 = not this clinic's draft).

    An explicit UPDATE rather than mutating a loaded object's attribute:
    SQLAlchemy's ORM flush emits `UPDATE outreach_drafts ... WHERE id = X`
    with no `clinic_id`, which the tenant guard rejects — correctly, since
    that statement would happily update another clinic's row if the id were
    wrong. Naming both predicates makes the scoping explicit and atomic.
    """
    if new_status not in DRAFT_STATUSES:
        raise ValueError(f"unknown draft status {new_status!r}")
    result = session.execute(
        update(OutreachDraft)
        .where(OutreachDraft.clinic_id == clinic_id, OutreachDraft.id == draft_id)
        .values(status=new_status)
    )
    return result.rowcount


def gate_reason_counts(session: Session, *, clinic_id: int) -> list[tuple[str, int]]:
    """How many clients each gate excluded, worst-first."""
    rows = session.execute(
        select(ClientDecision.gate_reason, func.count())
        .where(
            ClientDecision.clinic_id == clinic_id,
            ClientDecision.decided_by == "rule",
            ClientDecision.gate_reason.isnot(None),
        )
        .group_by(ClientDecision.gate_reason)
        .order_by(func.count().desc())
    ).all()
    return [(reason, count) for reason, count in rows]
