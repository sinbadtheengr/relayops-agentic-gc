"""Billing rules. Pure functions — no database, no model, no network.

This is the module a clinic will argue with, so every exclusion rule is
pinned and every billable line must be able to name its evidence.

Acceptance criteria for F-11 (see CLAUDE.md).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from relayops_fleet.core.attribution import (
    Contact,
    Outcome,
    attribute,
    render,
)

WINDOW = 30
FEE = 5_000       # $50.00
CAP = 150_000     # $1,500.00
DAY = date(2026, 6, 1)


def contact(key: str = "+1416", *, on: date = DAY, channel: str = "sms") -> Contact:
    return Contact(client_key=key, on=on, channel=channel)


def outcome(
    key: str = "+1416", *, name: str = "Dana", result: str = "showed", on: date = DAY
) -> Outcome:
    return Outcome(client_key=key, client_name=name, outcome=result, occurred_on=on)


def run(outcomes, contacts):
    return attribute(outcomes, contacts, window_days=WINDOW, fee_cents=FEE, cap_cents=CAP)


# --- What bills -----------------------------------------------------------


def test_a_show_after_a_contact_bills_once() -> None:
    s = run([outcome(on=DAY + timedelta(days=5))], [contact(on=DAY)])
    assert len(s.billable) == 1
    assert s.amount_cents == FEE


def test_a_billable_line_names_the_contact_that_earned_it() -> None:
    """A number a clinic cannot interrogate is a number they will dispute."""
    s = run([outcome(on=DAY + timedelta(days=5))], [contact(on=DAY, channel="sms")])
    line = s.billable[0]
    assert line.contacted_on == DAY
    assert line.channel == "sms"
    assert line.days_after_contact == 5


def test_the_latest_contact_before_the_visit_earns_it() -> None:
    """The message they most plausibly acted on, not the first one sent."""
    s = run(
        [outcome(on=DAY + timedelta(days=10))],
        [contact(on=DAY, channel="email"), contact(on=DAY + timedelta(days=8), channel="sms")],
    )
    assert s.billable[0].channel == "sms"
    assert s.billable[0].days_after_contact == 2


def test_same_day_contact_counts_as_prior() -> None:
    """Texted in the morning, came in the afternoon is the BEST outcome this
    product produces. Outcomes carry a date and contacts a timestamp, so
    same-day ordering is unknowable — excluding it would systematically
    under-bill the campaigns that worked best."""
    s = run([outcome(on=DAY)], [contact(on=DAY)])
    assert len(s.billable) == 1
    assert s.billable[0].days_after_contact == 0


# --- What does not bill ---------------------------------------------------


@pytest.mark.parametrize("result", ["booked", "no_show"])
def test_anything_that_is_not_a_show_does_not_bill(result: str) -> None:
    """A booking is not revenue. The fee is for a client who turned up."""
    s = run([outcome(result=result, on=DAY + timedelta(days=3))], [contact(on=DAY)])
    assert not s.billable
    assert "not a show" in s.excluded[0].reason


def test_a_show_with_no_contact_behind_it_does_not_bill() -> None:
    """They were coming anyway. Billing for it would be charging for weather."""
    s = run([outcome()], [])
    assert not s.billable
    assert "no logged contact" in s.excluded[0].reason


def test_a_contact_logged_after_the_visit_does_not_bill() -> None:
    s = run([outcome(on=DAY)], [contact(on=DAY + timedelta(days=2))])
    assert not s.billable
    assert "no logged contact" in s.excluded[0].reason


def test_a_visit_outside_the_window_does_not_bill() -> None:
    s = run([outcome(on=DAY + timedelta(days=WINDOW + 1))], [contact(on=DAY)])
    assert not s.billable
    assert f"outside the {WINDOW}d" in s.excluded[0].reason


def test_the_boundary_day_still_bills() -> None:
    s = run([outcome(on=DAY + timedelta(days=WINDOW))], [contact(on=DAY)])
    assert len(s.billable) == 1


# --- Per client, once -----------------------------------------------------


def test_book_no_show_then_rebook_and_attend_bills_exactly_once() -> None:
    """The acceptance case, and the one the sales script used to get wrong.

    It said both "per client who actually shows up" and "per booked-and-showed
    appointment" — one fee versus three for a client who rebooks. The code
    bills per client, and the copy now matches it.
    """
    s = run(
        [
            outcome(result="booked", on=DAY + timedelta(days=2)),
            outcome(result="no_show", on=DAY + timedelta(days=4)),
            outcome(result="showed", on=DAY + timedelta(days=11)),
            outcome(result="showed", on=DAY + timedelta(days=25)),
        ],
        [contact(on=DAY)],
    )
    assert len(s.billable) == 1
    assert s.amount_cents == FEE
    assert any("already billed once" in e.reason for e in s.excluded)


def test_the_first_show_is_the_billable_one() -> None:
    """Billing the later one would let a correction to an early appointment
    silently move the charge."""
    s = run(
        [
            outcome(result="showed", on=DAY + timedelta(days=20)),
            outcome(result="showed", on=DAY + timedelta(days=6)),
        ],
        [contact(on=DAY)],
    )
    assert s.billable[0].occurred_on == DAY + timedelta(days=6)


def test_two_different_clients_bill_separately() -> None:
    s = run(
        [
            outcome(key="+1a", name="Dana", on=DAY + timedelta(days=3)),
            outcome(key="+1b", name="Priya", on=DAY + timedelta(days=4)),
        ],
        [contact("+1a", on=DAY), contact("+1b", on=DAY)],
    )
    assert len(s.billable) == 2
    assert s.amount_cents == 2 * FEE


# --- The cap --------------------------------------------------------------


def test_the_cap_applies_and_the_waived_amount_is_visible() -> None:
    """A clinic that hits the cap should see what it was spared, not a
    silently smaller number."""
    outcomes = [
        outcome(key=f"+1{i}", name=f"C{i}", on=DAY + timedelta(days=2)) for i in range(40)
    ]
    contacts = [contact(f"+1{i}", on=DAY) for i in range(40)]
    s = run(outcomes, contacts)
    assert len(s.billable) == 40
    assert s.gross_cents == 40 * FEE
    assert s.capped
    assert s.amount_cents == CAP
    assert s.waived_cents == 40 * FEE - CAP


# --- Determinism and presentation ----------------------------------------


def test_attribution_is_recomputable() -> None:
    """Computed, never stored: the same evidence must give the same answer."""
    args = ([outcome(on=DAY + timedelta(days=5))], [contact(on=DAY)])
    first, second = run(*args), run(*args)
    assert first.amount_cents == second.amount_cents
    assert [b.client_key for b in first.billable] == [b.client_key for b in second.billable]


def test_exclusions_are_shown_not_filtered_away() -> None:
    """A clinic seeing eight shows and a bill for three needs to know why five
    did not count."""
    s = run(
        [
            outcome(key="+1a", name="Billed", on=DAY + timedelta(days=3)),
            outcome(key="+1b", name="NoContact", on=DAY + timedelta(days=3)),
            outcome(key="+1c", name="TooLate", on=DAY + timedelta(days=60)),
        ],
        [contact("+1a", on=DAY), contact("+1c", on=DAY)],
    )
    assert len(s.billable) == 1
    assert len(s.excluded) == 2
    text = render(s)
    assert "AMOUNT DUE: $50.00" in text
    assert "Not billed:" in text
    for excluded in s.excluded:
        assert excluded.reason, "every exclusion must carry a reason"


def test_empty_period_bills_nothing_without_erroring() -> None:
    s = run([], [])
    assert s.amount_cents == 0
    assert "AMOUNT DUE: $0.00" in render(s)
