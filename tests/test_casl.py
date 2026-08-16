"""CASL guard tests. No network, no model — the guards are pure functions.

Every guard is tested against copy that SHOULD trip it and copy that should
NOT. The second half is the half that matters: three separate false-positive
bugs have now been found in this class of guard (two in the F-1 spike, one in
an earlier relayops-prod audit), and each would have put a review badge on
correct copy.

Acceptance criteria for F-5 (see CLAUDE.md).
"""
from __future__ import annotations

import pytest

from relayops_fleet.core.casl import (
    EMAIL_FOOTER,
    NEEDS_REVIEW,
    STOP_LINE,
    apply_copy_guards,
    contains_discount_language,
    contains_overclaim,
    enforce_casl,
    flag_overclaims,
    flag_vip_discount,
)
from relayops_fleet.schemas import OutreachDraftSet


def draft(
    sms: str = "Hi Dana, we'd love to see you again {{booking_link}}",
    subject: str = "We miss you",
    body: str = "Hi Dana,\n\nIt has been a while — come see us.",
) -> OutreachDraftSet:
    return OutreachDraftSet(
        sms=sms, email_subject=subject, email_body=body, reasoning="test fixture"
    )


# --- CASL repair ----------------------------------------------------------


def test_sms_without_stop_line_gets_one_appended() -> None:
    """The model is never trusted to remember the STOP line."""
    out = enforce_casl(draft())
    assert STOP_LINE.lower() in out.sms.lower()


def test_email_without_unsubscribe_gets_the_footer_appended() -> None:
    out = enforce_casl(draft())
    assert out.email_body.endswith(EMAIL_FOOTER)
    assert "\n\n" in out.email_body, "footer must be separated to be readable"


def test_guards_are_idempotent() -> None:
    """Re-running enforcement on an already-compliant draft changes nothing.

    A redelivered Pub/Sub message must not stack two STOP lines.
    """
    once = enforce_casl(draft())
    twice = enforce_casl(once)
    assert once.sms == twice.sms
    assert once.email_body == twice.email_body
    assert twice.sms.lower().count(STOP_LINE.lower()) == 1
    assert twice.email_body.lower().count("unsubscribe") == 1


def test_compliant_draft_is_left_alone() -> None:
    compliant = draft(sms=f"Hi Dana! {STOP_LINE}.", body=f"Hi Dana,\n\n{EMAIL_FOOTER}")
    out = enforce_casl(compliant)
    assert out.sms == compliant.sms
    assert out.email_body == compliant.email_body


def test_repair_does_not_mangle_merge_fields() -> None:
    """The clinic fills these in; a guard that rewrites them breaks the send."""
    out = enforce_casl(draft())
    assert "{{booking_link}}" in out.sms
    assert "{{clinic_name}}" in out.email_body


# --- VIP discount guard ---------------------------------------------------


@pytest.mark.parametrize(
    "copy",
    [
        "Here's $25 off your next visit",
        "Enjoy a 20% off treatment",
        "We'd like to offer you a discount",
        "A welcome-back credit is waiting",
        "Book now for a free treatment",
        "Your {{incentive}} is ready",
    ],
)
def test_vip_draft_with_discount_language_is_flagged(copy: str) -> None:
    """Discounting an 80th-percentile spender reprices the client the clinic
    can least afford to reprice."""
    out, flagged = flag_vip_discount(draft(sms=copy), is_vip=True)
    assert flagged
    assert out.sms.startswith(NEEDS_REVIEW)


@pytest.mark.parametrize(
    "copy",
    [
        # From the F-1 spike: both produced by gemini-3.7/3.6-flash and both
        # compliant. The inherited regex flagged each of them.
        "Segment A VIP we-miss-you, non-discount perk",
        "VIP we-miss-you, no incentive",
        "A priority booking with no discount attached",
        # From an earlier relayops-prod guard audit.
        "Feel free to reply and let me know.",
        "Please feel free to call us anytime.",
        "The credit card we have on file is still valid.",
        # Ordinary compliant VIP copy.
        "We'd love to hold a priority spot for you.",
    ],
)
def test_compliant_vip_copy_is_not_flagged(copy: str) -> None:
    """The half of the test suite that actually prevents badge fatigue."""
    out, flagged = flag_vip_discount(draft(sms=copy, body=copy), is_vip=True)
    assert not flagged, f"false positive on: {copy}"
    assert not out.sms.startswith(NEEDS_REVIEW)


def test_non_vip_discount_is_allowed() -> None:
    """Segment C clients legitimately get a welcome-back credit."""
    out, flagged = flag_vip_discount(draft(sms="Here's $25 off"), is_vip=False)
    assert not flagged
    assert not out.sms.startswith(NEEDS_REVIEW)


def test_flag_marks_only_the_offending_field() -> None:
    out, flagged = flag_vip_discount(draft(sms="Here's $25 off"), is_vip=True)
    assert flagged
    assert out.sms.startswith(NEEDS_REVIEW)
    assert not out.email_body.startswith(NEEDS_REVIEW)


# --- Overclaim guard ------------------------------------------------------


@pytest.mark.parametrize(
    "copy",
    [
        "Results guaranteed or your money back",
        "This treatment is risk-free",
        "A permanent result, every time",
        "We're the best in Toronto",
        "The #1 clinic in the GTA",
    ],
)
def test_overclaim_language_is_flagged(copy: str) -> None:
    """'guaranteed results' on a medical service is a liability, not copy."""
    _out, flagged = flag_overclaims(draft(sms=copy))
    assert flagged


@pytest.mark.parametrize(
    "copy",
    [
        "We cannot guarantee availability, so book early",
        "Your comfort is our priority",
        "Come see what's new this season",
    ],
)
def test_ordinary_copy_is_not_an_overclaim(copy: str) -> None:
    """'We cannot guarantee availability' is the opposite of an overclaim.

    It contains the bare word, so the first version of this guard flagged it.
    Hedges are stripped before matching, exactly like negated offers.
    """
    _out, flagged = flag_overclaims(draft(sms=copy, subject="Hello", body=copy))
    assert not flagged, f"false positive on: {copy}"


def test_overclaim_checks_the_subject_line_too() -> None:
    """A subject line is a commercial message like any other."""
    _out, flagged = flag_overclaims(draft(subject="Guaranteed results inside"))
    assert flagged


# --- Composition ----------------------------------------------------------


def test_apply_copy_guards_repairs_and_reports() -> None:
    guarded = apply_copy_guards(draft(sms="Here's $25 off"), is_vip=True)
    assert guarded.needs_review
    assert "discount language in VIP draft" in guarded.reasons
    # Repair still happened despite the flag.
    assert STOP_LINE.lower() in guarded.draft.sms.lower()
    assert "unsubscribe" in guarded.draft.email_body.lower()


def test_apply_copy_guards_clean_draft_needs_no_review() -> None:
    guarded = apply_copy_guards(draft(), is_vip=True)
    assert not guarded.needs_review
    assert guarded.reasons == ()


def test_appended_footer_is_not_itself_scanned_for_offers() -> None:
    """CASL repair runs first; its own trusted text must not trip a guard."""
    guarded = apply_copy_guards(draft(), is_vip=True)
    assert EMAIL_FOOTER in guarded.draft.email_body
    assert not guarded.needs_review


def test_both_guards_can_fire_together() -> None:
    guarded = apply_copy_guards(
        draft(sms="Guaranteed results, plus $25 off"), is_vip=True
    )
    assert guarded.needs_review
    assert len(guarded.reasons) == 2


# --- The matcher itself ---------------------------------------------------


def test_negation_stripping_is_not_over_eager() -> None:
    """Stripping 'no discount' must not also swallow a real offer nearby."""
    assert contains_discount_language("No discount, but here is $25 off")
    assert not contains_discount_language("No discount and no incentive")


def test_overclaim_and_discount_matchers_are_independent() -> None:
    assert contains_overclaim("risk-free")
    assert not contains_discount_language("we cannot guarantee availability")
