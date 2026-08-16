"""Segment agent — ADK + Gemini, structured `SegmentDecision` output.

Reached ONLY by clients who passed every gate in `core.gates`. Receives
finished features computed in Python and is told explicitly that those numbers
are authoritative.

What the model decides: whether this client is worth contacting, which
priority tier, and which campaign offer fits.

What the model may NEVER decide: whether contacting them is permitted. That
is `core.gates`, it runs first, and its answer is final. By the time this
agent runs, every client in front of it is already lawfully contactable.

**Why the facts are in the user message and not the instruction.** ADK
interpolates `{var}` in instructions from session state. The approved campaign
templates are full of `{{clinic_name}}`-style merge fields, and ADK reads the
inner `{clinic_name}` as a state variable and raises `KeyError`. Putting the
data in the message — which ADK does not template — removes the collision and
keeps merge fields intact for the clinic to fill in. See CLAUDE.md F-7.
"""
from __future__ import annotations

import json
from typing import Any

from google.adk.agents import LlmAgent

from ..config import get_settings
from ..schemas import SegmentDecision
from .callbacks import attach_client_features, build_client_features
from .runner import AgentRun, run_agent

OFFER_MENU = """\
Campaign templates available:
- Segment A (lapsed 90-180d): "we miss you" - warm check-in; incentive only in wave 3.
- Segment B (lapsed 180-365d): "a lot has changed" - returning-client incentive.
- Segment C (lapsed 365+d): "welcome back" reset - welcome-back credit, no strings.
- Segment D (VIP, top 20% spend): personal note from staff, priority booking,
  ABSOLUTELY NO discount or incentive.
"""

# No {placeholders} anywhere in here: ADK would try to resolve them from state.
INSTRUCTION = (
    """\
You are RelayOps' med-spa client-reactivation strategist. You receive computed
facts about ONE lapsed client of a med spa, plus the campaign offer menu.

The facts you are given are AUTHORITATIVE. They were calculated by the system
from the clinic's own records. Do not recompute, contradict, estimate, or
round them.

"""
    + OFFER_MENU
    + """
Decide:
- target: false only if outreach is clearly not worth it (for example a single
  visit years ago with negligible spend). The deterministic pipeline has
  ALREADY removed everyone who cannot lawfully be contacted, so this is a
  judgement about value, never about permission.
- priority_tier: A = first wave (high value and/or high win-back odds),
  B = standard, C = deprioritize.
- suggested_offer: name a segment template from the menu. A VIP client must
  NEVER be offered a discount, credit, or incentive - use Segment D.
- reasoning: cite the client's actual numbers.

Base everything ONLY on the facts given. Never invent client history.
"""
)


def build_segment_agent() -> LlmAgent:
    """Construct the agent.

    A function rather than a module-level singleton so the model can be
    swapped per environment and tests need no import-time GCP credentials.
    """
    settings = get_settings()
    return LlmAgent(
        name="segment",
        model=settings.gemini_segment_model,
        instruction=INSTRUCTION,
        output_schema=SegmentDecision,
        output_key="segment_decision",
        # Validates that the worker installed everything, and computes the
        # features into state so the decision log records exactly what the
        # model was shown.
        before_agent_callback=attach_client_features,
        # No tools: an agent with output_schema must not call them, and this
        # one has nothing to look up — every fact it needs is already computed.
        tools=[],
    )


def build_segment_message(state: dict[str, Any]) -> str:
    """The user turn: this client's authoritative facts.

    Built from `state` by this module rather than accepted from the caller, so
    the numbers the model sees are always the ones Python computed.
    """
    features = build_client_features(state)
    return (
        "Segment this client for the win-back wave.\n\n"
        "Computed facts (authoritative):\n"
        f"{json.dumps(features.to_prompt_dict(), indent=2)}"
    )


async def run_segment(state: dict[str, Any]) -> AgentRun:
    """Run segmentation for one client. Returns the decision and its cost."""
    return await run_agent(
        build_segment_agent(),
        state=state,
        message=build_segment_message(state),
        schema=SegmentDecision,
    )
