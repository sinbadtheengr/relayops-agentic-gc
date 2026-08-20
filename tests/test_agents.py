"""Agent wiring, template selection, and message construction.

No model call unless RELAYOPS_TEST_LLM=1 — the wiring is what breaks, and it
breaks deterministically. The live test at the bottom is the acceptance
criterion for F-7.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from relayops_fleet.agents import outreach, segment
from relayops_fleet.agents.callbacks import (
    AS_OF,
    CLIENT_ROW,
    VIP_CUTOFF_CENTS,
    MissingAgentInput,
    build_client_features,
)
from relayops_fleet.core.templates import TemplateSectionMissing, load_template_section
from relayops_fleet.schemas import OutreachDraftSet, SegmentDecision

VIP_STATE = {
    CLIENT_ROW: {
        "first_name": "Dana",
        "last_visit": "2026-01-03",
        "visit_count": 7,
        "lifetime_spend_cents": 412_000,
        "last_service": "injectables",
    },
    VIP_CUTOFF_CENTS: 280_000,
    AS_OF: "2026-08-16",
}

REGULAR_STATE = {
    **VIP_STATE,
    CLIENT_ROW: {**VIP_STATE[CLIENT_ROW], "lifetime_spend_cents": 40_000},
}


# --- Template selection ---------------------------------------------------


@pytest.mark.parametrize(
    ("bucket", "expected"),
    [
        ("lapsed_90_180", "## Segment A"),
        ("lapsed_180_365", "## Segment B"),
        ("lapsed_365_plus", "## Segment C"),
    ],
)
def test_bucket_selects_its_section(bucket: str, expected: str) -> None:
    assert expected in load_template_section(bucket=bucket, is_vip=False)


def test_vip_overrides_the_bucket() -> None:
    """Segment D is the only section with no incentive in it.

    A VIP routed to their bucket's section would be shown copy offering money
    off, which is the one thing a VIP draft must never contain.
    """
    section = load_template_section(bucket="lapsed_365_plus", is_vip=True)
    assert "## Segment D" in section
    assert "NO discount" in section


def test_unknown_bucket_raises_rather_than_returning_everything() -> None:
    """relayops-prod fell back to the whole document on a miss, which hands
    the model every segment's copy — including discounts — and invites it to
    pick one."""
    with pytest.raises(TemplateSectionMissing):
        load_template_section(bucket=None, is_vip=False)


def test_vip_section_contains_no_incentive_merge_field() -> None:
    section = load_template_section(bucket=None, is_vip=True)
    assert "{{incentive}}" not in section


# --- Callback contract ----------------------------------------------------


def test_missing_state_raises_rather_than_guessing() -> None:
    """An agent reasoning over a missing spend would quietly mis-tier a client
    and the failure would surface weeks later as a bad campaign."""
    for missing in (CLIENT_ROW, VIP_CUTOFF_CENTS, AS_OF):
        state = {k: v for k, v in VIP_STATE.items() if k != missing}
        with pytest.raises(MissingAgentInput):
            build_client_features(state)


def test_features_are_computed_not_taken_from_the_caller() -> None:
    """The caller supplies raw rows; is_vip is derived here.

    A caller that passed is_vip directly could hand the agent a value that
    came from anywhere, including an earlier model call.
    """
    assert build_client_features(VIP_STATE).is_vip
    assert not build_client_features(REGULAR_STATE).is_vip
    assert CLIENT_ROW in VIP_STATE and "is_vip" not in VIP_STATE[CLIENT_ROW]


# --- Message construction -------------------------------------------------


def test_segment_message_carries_the_authoritative_numbers() -> None:
    message = segment.build_segment_message(VIP_STATE)
    assert "412000" in message
    assert "280000" in message
    assert "authoritative" in message.lower()


def test_outreach_message_includes_the_approved_section() -> None:
    message = outreach.build_outreach_message(VIP_STATE)
    assert "## Segment D" in message
    assert "412000" in message


def test_outreach_message_carries_campaign_memory_when_there_is_any() -> None:
    """Memory reaches the prompt through state, installed by the caller."""
    from relayops_fleet.agents.callbacks import CAMPAIGN_MEMORY

    fact = "Segment D copy sent by SMS to VIP clients lapsed over a year: 5 contacted, 4 booked and showed within 30 days (80% of those contacted)."
    message = outreach.build_outreach_message({**VIP_STATE, CAMPAIGN_MEMORY: [fact]})
    assert fact in message
    assert "TONE and EMPHASIS only" in message


def test_outreach_memory_block_sits_after_the_approved_template() -> None:
    """The block claims the section above is the only source of an offer.

    That sentence is only true if the section is in fact above it, so the
    ordering is part of the guarantee rather than a formatting preference.
    """
    from relayops_fleet.agents.callbacks import CAMPAIGN_MEMORY

    fact = "Segment D copy sent by SMS to VIP clients lapsed over a year: 5 contacted, 4 booked and showed within 30 days (80% of those contacted)."
    message = outreach.build_outreach_message({**VIP_STATE, CAMPAIGN_MEMORY: [fact]})
    assert message.index("## Segment D") < message.index(fact)


def test_outreach_message_is_unchanged_when_there_is_no_memory() -> None:
    """A clinic with no history must not get an empty "what converted" heading."""
    from relayops_fleet.agents.callbacks import CAMPAIGN_MEMORY

    plain = outreach.build_outreach_message(VIP_STATE)
    assert outreach.build_outreach_message({**VIP_STATE, CAMPAIGN_MEMORY: []}) == plain
    assert "What has converted" not in plain


def test_outreach_refuses_a_client_outside_the_lapse_buckets() -> None:
    """Such a client should never have reached outreach; drafting from an
    arbitrary template would be inventing an offer."""
    recent = {**REGULAR_STATE, CLIENT_ROW: {**REGULAR_STATE[CLIENT_ROW], "last_visit": "2026-08-01"}}
    with pytest.raises(TemplateSectionMissing):
        outreach.build_outreach_message(recent)


# --- Instruction hygiene --------------------------------------------------


@pytest.mark.parametrize("instruction", [segment.INSTRUCTION, outreach.INSTRUCTION])
def test_instructions_contain_no_template_variables(instruction: str) -> None:
    """ADK interpolates {var} in instructions from session state.

    The approved campaign templates are full of {{merge_field}} placeholders,
    and ADK reads the inner {merge_field} as a state variable and raises
    KeyError. That is why the facts and the template travel in the user
    message instead — this test stops anyone reintroducing a brace here.
    """
    import re

    assert not re.search(r"\{[A-Za-z_]", instruction), "instruction contains a {variable}"


# --- Agent construction ---------------------------------------------------


def test_segment_agent_is_wired_to_the_right_model_and_schema() -> None:
    agent = segment.build_segment_agent()
    assert agent.output_schema is SegmentDecision
    assert agent.before_agent_callback is not None
    assert not agent.tools, "an agent with output_schema must not call tools"


def test_outreach_agent_is_wired_to_the_right_model_and_schema() -> None:
    agent = outreach.build_outreach_agent()
    assert agent.output_schema is OutreachDraftSet
    assert agent.before_agent_callback is not None
    assert not agent.tools


# --- Live acceptance (F-7) ------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("RELAYOPS_TEST_LLM"),
    reason="set RELAYOPS_TEST_LLM=1 to make real Vertex calls",
)
def test_live_segment_cites_the_injected_facts() -> None:
    run = asyncio.run(segment.run_segment(dict(VIP_STATE)))
    decision = run.output
    assert isinstance(decision, SegmentDecision)
    assert run.tokens > 0
    # The model can only know this number if the computed facts reached it.
    assert "412000" in decision.reasoning.replace(",", "")


@pytest.mark.skipif(
    not os.environ.get("RELAYOPS_TEST_LLM"),
    reason="set RELAYOPS_TEST_LLM=1 to make real Vertex calls",
)
def test_live_vip_draft_carries_no_discount_and_keeps_merge_fields() -> None:
    from relayops_fleet.core.casl import apply_copy_guards

    run = asyncio.run(outreach.run_outreach(dict(VIP_STATE)))
    guarded = apply_copy_guards(run.output, is_vip=True)

    assert not guarded.needs_review, f"guards flagged a VIP draft: {guarded.reasons}"
    combined = guarded.draft.sms + guarded.draft.email_body
    assert "{{" in combined, "merge fields were not preserved for the clinic to fill"
    assert "reply stop to opt out" in guarded.draft.sms.lower()


def test_templates_are_findable_via_env_override(tmp_path, monkeypatch) -> None:
    """The container installs the package non-editable, so the repo-relative
    walk resolves under site-packages and finds nothing. RELAYOPS_TEMPLATES_DIR
    is how the image points at the copy baked into it. A missing template only
    surfaces at draft time, which is far too late to discover it."""
    import importlib

    from relayops_fleet.core import templates as tmod

    src = Path(tmod.TEMPLATES_PATH).read_text(encoding="utf-8")
    (tmp_path / "campaign-templates.md").write_text(src, encoding="utf-8")
    monkeypatch.setenv("RELAYOPS_TEMPLATES_DIR", str(tmp_path))
    importlib.reload(tmod)
    try:
        assert tmod.TEMPLATES_PATH.parent == tmp_path
        assert "## Segment D" in tmod.load_template_section(bucket=None, is_vip=True)
    finally:
        monkeypatch.delenv("RELAYOPS_TEMPLATES_DIR", raising=False)
        importlib.reload(tmod)
