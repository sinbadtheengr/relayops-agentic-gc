"""Outreach agent — ADK + Gemini, structured `OutreachDraftSet` output.

Adapts the approved campaign-template section into SMS + email copy for one
client. Output passes through `core.casl` guards before it is persisted, and
lands with `status='draft'`.

**This system never sends.** There is no send path in this repository. Approval
marks a draft; a human sends it out of band and clicks Mark sent, which writes
`contact_log` (starting the cooldown) BEFORE flipping the status — so a failure
can never produce a sent draft whose cooldown silently did not start.

The approved template travels in the user message, not the instruction: it is
full of `{{merge_field}}` placeholders that ADK's instruction templating would
try to resolve from session state. See `segment.py` for the full note.

See CLAUDE.md F-7.
"""
from __future__ import annotations

import json
from typing import Any

from google.adk.agents import LlmAgent

from ..config import get_settings
from ..core.templates import load_template_section
from ..schemas import OutreachDraftSet
from .callbacks import attach_template_section, build_client_features
from .runner import AgentRun, run_agent

# No {placeholders}: ADK would try to resolve them from state. The merge-field
# examples below are described in prose for the same reason.
INSTRUCTION = """\
You write win-back outreach for a med spa, in the voice of a well-liked
front-desk person texting a regular. Warm, specific, never salesy.

You are given the client's computed facts (AUTHORITATIVE - calculated by the
system from the clinic's own records; do not recompute or contradict them) and
an APPROVED campaign template section. The template is the clinic's signed-off
voice: adapt it, and do not invent a different offer.

Rules, none of them optional:
- One ask per message.
- Merge fields are written in double curly braces, for example clinic_name,
  booking_link, staff_name, clinic_address and incentive. Copy them through
  EXACTLY as they appear in the template, still in their double braces - the
  clinic fills them in later. Write out the client's first name normally.
- SMS: under 320 characters, ending with "Reply STOP to opt out".
- Email body: short paragraphs separated by blank lines, ending with the
  approved footer line after a blank line. Never return one run-on block.
- A VIP client gets a personal, gratitude-led, priority-booking message with
  ABSOLUTELY NO discount, credit, incentive, or "free" anything.
- Never promise a medical result, and never invent client history beyond the
  facts given.
"""


def build_outreach_agent() -> LlmAgent:
    """Construct the agent. See build_segment_agent for why this is a function."""
    settings = get_settings()
    return LlmAgent(
        name="outreach",
        model=settings.gemini_outreach_model,
        instruction=INSTRUCTION,
        output_schema=OutreachDraftSet,
        output_key="outreach_draft",
        before_agent_callback=attach_template_section,
        tools=[],
    )


def build_outreach_message(state: dict[str, Any]) -> str:
    """The user turn: authoritative facts plus the approved template section.

    Raises via `load_template_section` if the client falls outside the lapse
    buckets — such a client should never have reached outreach, and drafting
    from an arbitrary template would be inventing an offer.
    """
    features = build_client_features(state)
    section = load_template_section(bucket=features.lapse_bucket, is_vip=features.is_vip)
    return (
        "Draft the wave-1 SMS and email for this client.\n\n"
        "Computed facts (authoritative):\n"
        f"{json.dumps(features.to_prompt_dict(), indent=2)}\n\n"
        "Approved campaign template section:\n"
        f"{section}"
    )


async def run_outreach(state: dict[str, Any]) -> AgentRun:
    """Draft outreach for one targeted client. Returns the drafts and cost."""
    return await run_agent(
        build_outreach_agent(),
        state=state,
        message=build_outreach_message(state),
        schema=OutreachDraftSet,
    )
