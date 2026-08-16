"""F-1 step 4 — prove the real F-7 agent shape runs on ADK, not just a hello-world.

Exercises exactly what the segment agent will do in production:
  - an ADK LlmAgent on a qualifying Gemini model
  - `output_schema=SegmentDecision` (strict, extra='forbid')
  - a `before_agent_callback` that injects Python-computed facts into session
    state, so the model interprets rather than calculates

    PYTHONPATH=src python scripts/spike_f1_adk.py
"""
from __future__ import annotations

import asyncio
import json
import os

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.runners import InMemoryRunner
from google.genai import types

from relayops_fleet.schemas import SegmentDecision

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "relayops-fleet")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

APP = "relayops-fleet-spike"
MODEL = os.environ.get("GEMINI_SEGMENT_MODEL", "gemini-3.7-flash")

# Stands in for core/features.py. In production these come from Postgres and
# are computed per clinic; here they are inline so the spike needs no database.
COMPUTED_FEATURES = {
    "first_name": "Dana",
    "days_lapsed": 231,
    "lapse_bucket": "9-12 months",
    "visit_count": 7,
    "lifetime_spend_cents": 412000,
    "is_vip": True,
    "vip_threshold_cents": 280000,
    "last_service": "injectables",
}

INSTRUCTION = """You segment lapsed clients for a med spa's win-back campaign.

The client's computed facts are below. They are AUTHORITATIVE — they were
calculated by the system from the clinic's own records. Do not recompute,
contradict, or estimate them.

{computed_facts}

Rules:
- VIP clients never receive discount offers. A VIP is an 80th-percentile
  spender; discounting trains them to wait for the discount.
- Never promise a medical result.
- suggested_offer must name a campaign template, not invent a promotion.
- reasoning must cite the client's actual numbers.
"""


def attach_client_features(callback_context: CallbackContext) -> None:
    """before_agent_callback — the authoritative-facts injection (F-7).

    Writing into state (rather than into the user message) is what lets the
    deterministic core stay the single source of these numbers: the prompt
    template reads {computed_facts} from state, so there is exactly one place
    they can come from.
    """
    callback_context.state["computed_facts"] = json.dumps(COMPUTED_FEATURES, indent=2)


segment_agent = LlmAgent(
    name="segment",
    model=MODEL,
    instruction=INSTRUCTION,
    output_schema=SegmentDecision,
    output_key="decision",
    before_agent_callback=attach_client_features,
)


async def main() -> None:
    runner = InMemoryRunner(agent=segment_agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id="operator")

    final = None
    async for event in runner.run_async(
        user_id="operator",
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part(text="Segment this client for the win-back wave.")]
        ),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final = event.content.parts[0].text

    print(f"model={MODEL}  location={os.environ['GOOGLE_CLOUD_LOCATION']}")
    if not final:
        raise SystemExit("FAIL: no final response")

    # Strict parse — this is the real gate.
    decision = SegmentDecision.model_validate_json(final)
    print(f"  target={decision.target} tier={decision.priority_tier}")
    print(f"  offer={decision.suggested_offer}")
    print(f"  reasoning={decision.reasoning[:150]}")

    # Confirm the callback actually reached the prompt: the model can only
    # know these numbers if state injection worked.
    cited = str(COMPUTED_FEATURES["lifetime_spend_cents"]) in decision.reasoning
    print(f"\n  state-injection confirmed (cites lifetime spend): {cited}")
    print("\nPASS" if cited else "\nFAIL: model did not cite injected facts")


if __name__ == "__main__":
    asyncio.run(main())
