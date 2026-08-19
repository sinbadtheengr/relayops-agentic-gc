"""De-identification and local name re-join. Pure — no model, no network.

The agents never learn who they are writing to. These tests pin that, and pin
the re-join that makes the copy usable anyway.

Acceptance criteria for GAP-014.
"""
from __future__ import annotations

import pytest

from relayops_fleet.agents import outreach, segment
from relayops_fleet.agents.callbacks import (
    AS_OF,
    CLIENT_ROW,
    VIP_CUTOFF_CENTS,
    build_client_features,
)
from relayops_fleet.core.personalize import (
    apply_merge_fields,
    substitute_first_name,
    unsubstituted_tokens,
)
from relayops_fleet.schemas import OutreachDraftSet

NAME = "Dana"

STATE = {
    CLIENT_ROW: {
        "first_name": NAME,
        "last_visit": "2026-01-03",
        "visit_count": 7,
        "lifetime_spend_cents": 412_000,
        "last_service": "injectables",
        "notes": "Prefers afternoon appointments.",
    },
    VIP_CUTOFF_CENTS: 280_000,
    AS_OF: "2026-08-16",
}


def draft(
    sms: str = "Hi {{first_name}}, come see us. Reply STOP to opt out.",
    subject: str = "It's been a minute, {{first_name}}",
    body: str = "Hi {{first_name}},\n\nWe miss you.",
) -> OutreachDraftSet:
    return OutreachDraftSet(sms=sms, email_subject=subject, email_body=body, reasoning="t")


# --- The name never reaches the model -------------------------------------


def test_computed_facts_carry_no_name() -> None:
    """A name is an identifier; lapse and spend are attributes. Only the
    attributes may leave the process."""
    facts = build_client_features(STATE).to_prompt_dict()
    assert "first_name" not in facts
    assert NAME not in str(facts)


def test_the_segment_prompt_contains_no_name() -> None:
    assert NAME not in segment.build_segment_message(STATE)


def test_the_outreach_prompt_contains_no_name() -> None:
    """Gemini >=3.5 is global-endpoint only, so this prompt may be processed
    outside Canada. The honest answer to "where do the names go?" is that they
    do not."""
    assert NAME not in outreach.build_outreach_message(STATE)


def test_the_outreach_prompt_still_carries_the_merge_field() -> None:
    """The model must have something to address the client as."""
    assert "{{first_name}}" in outreach.build_outreach_message(STATE)


def test_no_client_identifier_of_any_kind_reaches_the_prompt() -> None:
    message = outreach.build_outreach_message(STATE)
    for identifier in (NAME, "+14165550101", "dana@example.com"):
        assert identifier not in message


# --- The local re-join ----------------------------------------------------


def test_every_field_gets_the_name_including_the_subject() -> None:
    """An unsubstituted placeholder in a subject line is the most visible way
    to look automated to the person you are winning back."""
    out = apply_merge_fields(draft(), first_name=NAME)
    assert f"Hi {NAME}," in out.sms
    assert out.email_subject == f"It's been a minute, {NAME}"
    assert f"Hi {NAME}," in out.email_body
    assert not unsubstituted_tokens(out)


@pytest.mark.parametrize(
    "token",
    ["{{first_name}}", "{{ first_name }}", "{{First_Name}}", "{{  FIRST_NAME  }}"],
)
def test_whitespace_and_case_slips_still_substitute(token: str) -> None:
    """The model copies this token through by hand; a slip must not ship a
    literal placeholder to a real client."""
    assert substitute_first_name(f"Hi {token}!", NAME) == f"Hi {NAME}!"


def test_the_clinics_own_merge_fields_are_left_alone() -> None:
    """clinic_name, booking_link and the rest belong to whoever sends the
    message, not to this system."""
    out = apply_merge_fields(
        draft(sms="Hi {{first_name}}, book at {{booking_link}} with {{staff_name}}."),
        first_name=NAME,
    )
    assert "{{booking_link}}" in out.sms
    assert "{{staff_name}}" in out.sms
    assert "{{first_name}}" not in out.sms


def test_unsubstituted_tokens_detects_a_survivor() -> None:
    assert unsubstituted_tokens(draft())
    assert not unsubstituted_tokens(apply_merge_fields(draft(), first_name=NAME))


def test_a_draft_with_no_placeholder_is_unchanged() -> None:
    plain = draft(sms="Hello there.", subject="Hello", body="Hello.")
    assert apply_merge_fields(plain, first_name=NAME).sms == "Hello there."
