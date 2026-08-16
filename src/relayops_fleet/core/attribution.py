"""Billing. Attribution is COMPUTED, never stored. NO LLM CALLS.

The pricing model is "$50 per client who books and shows up — each client
counts once — capped at $1,500". This module decides what a clinic owes,
which makes it the one output someone will argue with. It is built for that
argument.

A show bills because a logged contact preceded it inside the attribution
window. Recompute it and the same evidence gives the same answer, whereas a
stored flag would drift the moment a contact was corrected.

Every billable line names the contact that earned it and the gap in days, and
**excluded outcomes are returned with reasons rather than filtered away**: a
clinic seeing eight shows and a bill for three needs to know why five did not
count. A number they cannot interrogate is a number they will dispute.

Ported from relayops-prod `src/relayops/attribution.py` — see CLAUDE.md F-11.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

BILLABLE_OUTCOME = "showed"


@dataclass(frozen=True)
class Contact:
    """One logged outreach. `on` is the calendar date it was sent."""

    client_key: str
    on: date
    channel: str


@dataclass(frozen=True)
class Outcome:
    """One recorded appointment result."""

    client_key: str
    client_name: str
    outcome: str
    occurred_on: date


@dataclass(frozen=True)
class AttributedShow:
    client_key: str
    client_name: str
    occurred_on: date
    contacted_on: date
    channel: str
    days_after_contact: int


@dataclass(frozen=True)
class ExcludedOutcome:
    client_key: str
    client_name: str
    occurred_on: date
    outcome: str
    reason: str


@dataclass
class BillingSummary:
    window_days: int
    fee_cents: int
    cap_cents: int
    billable: list[AttributedShow] = field(default_factory=list)
    excluded: list[ExcludedOutcome] = field(default_factory=list)

    @property
    def gross_cents(self) -> int:
        return len(self.billable) * self.fee_cents

    @property
    def amount_cents(self) -> int:
        return min(self.gross_cents, self.cap_cents)

    @property
    def capped(self) -> bool:
        return self.gross_cents > self.cap_cents

    @property
    def waived_cents(self) -> int:
        return max(0, self.gross_cents - self.cap_cents)


def attribute(
    outcomes: list[Outcome],
    contacts: list[Contact],
    *,
    window_days: int,
    fee_cents: int,
    cap_cents: int,
) -> BillingSummary:
    """Decide what is billable, and say why for everything that is not.

    **Same-day contact counts as prior.** The outcome carries a calendar date
    while the contact carries a timestamp, so same-day ordering is not
    knowable from the data. Counting it as prior is the deliberate choice:
    texted in the morning, came in the afternoon is the *best* outcome this
    product produces, and excluding it would systematically under-bill the
    campaigns that worked best. Where the evidence is ambiguous the rule is
    stated rather than silently resolved.

    Outcomes are processed oldest-first, so when a client shows twice the
    FIRST show is the billable one. Billing the later one would let a
    correction to an early appointment silently move the charge.
    """
    summary = BillingSummary(window_days=window_days, fee_cents=fee_cents, cap_cents=cap_cents)

    by_client: dict[str, list[Contact]] = {}
    for contact in sorted(contacts, key=lambda c: c.on):
        by_client.setdefault(contact.client_key, []).append(contact)

    already_billed: set[str] = set()

    def exclude(item: Outcome, reason: str) -> None:
        summary.excluded.append(
            ExcludedOutcome(
                client_key=item.client_key,
                client_name=item.client_name,
                occurred_on=item.occurred_on,
                outcome=item.outcome,
                reason=reason,
            )
        )

    for outcome in sorted(outcomes, key=lambda o: (o.occurred_on, o.client_key)):
        if outcome.outcome != BILLABLE_OUTCOME:
            # A booking is not revenue. The fee is for a client who turned up.
            exclude(outcome, "not a show — the fee is per client who books AND attends")
            continue

        # The earning contact is the LATEST one at or before the appointment:
        # the message they most plausibly acted on.
        prior = [c for c in by_client.get(outcome.client_key, []) if c.on <= outcome.occurred_on]
        if not prior:
            exclude(
                outcome, "no logged contact before this appointment — they were coming anyway"
            )
            continue

        earning = prior[-1]
        gap = (outcome.occurred_on - earning.on).days
        if gap > window_days:
            exclude(
                outcome, f"{gap}d after contact, outside the {window_days}d attribution window"
            )
            continue

        if outcome.client_key in already_billed:
            exclude(outcome, "already billed once for this client this period")
            continue

        already_billed.add(outcome.client_key)
        summary.billable.append(
            AttributedShow(
                client_key=outcome.client_key,
                client_name=outcome.client_name,
                occurred_on=outcome.occurred_on,
                contacted_on=earning.on,
                channel=earning.channel,
                days_after_contact=gap,
            )
        )

    return summary


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def render(summary: BillingSummary) -> str:
    """Plain-text invoice, inclusions and exclusions together."""
    headline = (
        f"{len(summary.billable)} attributable show(s) x {money(summary.fee_cents)}"
        f" = {money(summary.gross_cents)}"
    )
    lines = [headline]
    if summary.capped:
        lines.append(
            f"  capped at {money(summary.cap_cents)} (waived {money(summary.waived_cents)})"
        )
    lines.append(f"  AMOUNT DUE: {money(summary.amount_cents)}")

    if summary.billable:
        lines += ["", "  Billable — each names the contact that earned it:"]
        for s in summary.billable:
            lines.append(
                f"    {s.client_name:<20} showed {s.occurred_on:%Y-%m-%d}"
                f"  <- {s.channel} on {s.contacted_on:%Y-%m-%d}"
                f" ({s.days_after_contact}d earlier)"
            )
    if summary.excluded:
        lines += ["", "  Not billed:"]
        for e in summary.excluded:
            lines.append(f"    {e.client_name:<20} {e.occurred_on:%Y-%m-%d}  {e.reason}")
    return "\n".join(lines)
