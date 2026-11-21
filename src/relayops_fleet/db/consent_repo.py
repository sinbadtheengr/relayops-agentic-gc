"""Loaders and writers for the consent registers.

Kept out of `core/` so `core.gates` stays a pure function over values: the
compliance decision is testable without a database, and this module is the
only place that knows where those values come from.

Scoping discipline, restated because getting it backwards is the whole risk:
  - opt-outs are loaded GLOBALLY (no clinic filter, by design)
  - cooldown is loaded PER CLINIC

See CLAUDE.md F-4.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.gates import e164, normalize_email
from .models import ContactLog, OptOut
from .repo import unguarded


def load_opt_outs(session: Session) -> tuple[frozenset[str], frozenset[str]]:
    """Every opt-out in the system, as (phones, emails).

    Deliberately unscoped — `opt_outs` has no clinic_id, and reading it
    through `unguarded()` states that on purpose rather than tripping the
    tenant guard by accident. A per-clinic read here would permit contacting
    someone who opted out at another clinic.
    """
    with unguarded():
        rows = session.execute(select(OptOut.client_key, OptOut.email)).all()
    return (
        frozenset(r.client_key for r in rows if r.client_key),
        frozenset(r.email.strip().lower() for r in rows if r.email),
    )


def recently_contacted_phones(
    session: Session, *, clinic_id: int, cooldown_days: int | None = None
) -> frozenset[str]:
    """Client keys THIS clinic contacted within the cooldown window.

    Scoped to `clinic_id`: a cooldown exists so one sender does not
    over-message someone. Before this was scoped in relayops-prod, one
    clinic's outreach put another clinic's same-phone customer into cooldown —
    breaking that campaign and leaking that the two clinics share a client.
    """
    days = cooldown_days if cooldown_days is not None else get_settings().contact_cooldown_days
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(
        select(ContactLog.client_key)
        .where(ContactLog.clinic_id == clinic_id, ContactLog.contacted_at >= cutoff)
        .distinct()
    ).all()
    return frozenset(r.client_key for r in rows)


def log_contact(
    session: Session,
    *,
    clinic_id: int,
    client_key: str,
    channel: str,
    note: str | None = None,
) -> None:
    """Append-only record that a client was actually contacted; starts cooldown.

    Written BEFORE a draft's status flips to 'sent' (F-8), so a failure can
    never produce a sent draft whose cooldown silently did not start.
    """
    session.add(
        ContactLog(clinic_id=clinic_id, client_key=client_key, channel=channel, note=note)
    )


def record_opt_out(
    session: Session,
    *,
    phone: str | None = None,
    email: str | None = None,
    reason: str | None = None,
    source: str = "manual",
) -> OptOut:
    """Register a permanent do-not-contact on a phone, an email, or both.

    Raises if neither identifier is usable. Recording an opt-out that matches
    nothing is worse than failing loudly: the person believes they opted out,
    and the next campaign proves otherwise.
    """
    phone_norm = e164(phone)
    email_norm = normalize_email(email)
    if phone_norm is None and email_norm is None:
        raise ValueError("an opt-out needs a valid phone or an email")

    # A repeat on one identifier can introduce a newly disclosed second one.
    # Insert each independently so both are suppressed, even on concurrent retries.
    with unguarded():
        for column, value in ((OptOut.client_key, phone_norm), (OptOut.email, email_norm)):
            if value:
                session.execute(insert(OptOut).values(
                    {column.key:value, "reason":reason, "source":source}
                ).on_conflict_do_nothing())
        criterion = OptOut.client_key == phone_norm if phone_norm else OptOut.email == email_norm
        return session.scalars(select(OptOut).where(criterion)).one()


def main():
    import argparse
    from .repo import build_engine

    parser = argparse.ArgumentParser(description="Record an email unsubscribe or SMS withdrawal")
    parser.add_argument("--email")
    parser.add_argument("--phone")
    parser.add_argument("--source", required=True, choices=["email", "sms", "manual"])
    parser.add_argument("--reason", required=True, help="Receipt time and reference to the request")
    args = parser.parse_args()
    with Session(build_engine()) as session:
        record_opt_out(session, **vars(args))
        session.commit()


if __name__ == "__main__":
    main()
