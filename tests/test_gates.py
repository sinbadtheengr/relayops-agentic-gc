"""Gate tests — the highest-value tests in the repo.

These run with no database, no network and no model. That is the point: the
compliance boundary is pure functions, so it is exhaustively testable and a
regression in it is impossible to miss.

Acceptance criteria for F-4 (see CLAUDE.md):
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="F-4 not implemented")


def test_opt_out_is_global_not_per_clinic() -> None:
    """A client who opted out at clinic A is gated at clinic B.

    Under-suppressing is the compliance risk; over-suppressing costs a lead.
    """


def test_cooldown_is_per_clinic() -> None:
    """Clinic A contacting a shared client does not put clinic B into cooldown.

    The inverse would break clinic B's campaign AND leak that the two clinics
    share a customer.
    """


def test_gated_client_never_reaches_the_model() -> None:
    """A gated client produces decided_by='rule' with a reason and zero tokens."""


def test_blank_last_visit_is_skipped_not_defaulted() -> None:
    """A blank date must not make a client look maximally lapsed."""


def test_unparseable_phone_is_skipped_with_reason() -> None:
    """No silent drop — every exclusion is recorded."""
