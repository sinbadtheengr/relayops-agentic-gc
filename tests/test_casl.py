"""CASL guard tests. No network, no model — the guards are pure functions.

Acceptance criteria for F-5 (see CLAUDE.md):
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="F-5 not implemented")


def test_sms_without_stop_line_gets_one_appended() -> None:
    """The model is never trusted to remember the STOP line."""


def test_email_without_unsubscribe_gets_the_footer_appended() -> None:
    ...


def test_vip_draft_with_discount_language_is_flagged_needs_review() -> None:
    """Discounting an 80th-percentile spender reprices the client the clinic
    can least afford to reprice."""


def test_overclaim_language_is_flagged_needs_review() -> None:
    """'guaranteed results' on a medical service is a liability, not copy."""


def test_guards_are_idempotent() -> None:
    """Re-running enforcement on an already-compliant draft changes nothing —
    a redelivered Pub/Sub message must not stack two STOP lines."""
