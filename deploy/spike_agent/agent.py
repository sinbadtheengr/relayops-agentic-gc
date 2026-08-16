"""F-1 step 4 — the minimum deployable agent that proves the stack end to end.

Deliberately the same shape as the production segment agent (qualifying model,
strict output schema, before_agent_callback injecting Python-computed facts),
so a successful Cloud Run deploy proves the real architecture works there and
not merely that a container can serve text.

Deployed by:
    adk deploy cloud_run --project relayops-fleet --region us-central1 \
        --service_name relayops-fleet-spike deploy/spike_agent

Note the two different locations, which are independent and both correct:
  - Cloud Run region  = us-central1 (where the container runs)
  - Vertex location   = global      (the only endpoint serving Gemini >=3.5)
"""
from __future__ import annotations

import json
import os
from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from pydantic import BaseModel, ConfigDict, Field

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")

# HARD ASSIGN, not setdefault. `adk deploy cloud_run` injects
# GOOGLE_CLOUD_LOCATION=<deploy region> into the container, so a setdefault is
# a no-op and the agent asks us-central1 for a global-only model — a 404 that
# only appears once deployed. The Cloud Run region and the Vertex location are
# different things and must never share a variable.
os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ.get("VERTEX_LOCATION", "global")

MODEL = os.environ.get("GEMINI_SEGMENT_MODEL", "gemini-3.7-flash")


# Duplicated from relayops_fleet.schemas rather than imported: `adk deploy`
# packages only this folder, so the agent must be self-contained. The spike
# is the one place this duplication is acceptable — F-7 deploys the real
# package instead.
class SegmentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: bool = Field(description="Whether to include this client in the win-back campaign")
    priority_tier: Literal["A", "B", "C"] = Field(description="A = contact first")
    suggested_offer: str = Field(description="Campaign template; VIPs never get discounts")
    reasoning: str = Field(description="Grounded justification citing the client's actual numbers")


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

The client's computed facts are below. They are AUTHORITATIVE — calculated by
the system from the clinic's own records. Do not recompute or contradict them.

{computed_facts}

Rules:
- VIP clients never receive discount offers.
- Never promise a medical result.
- suggested_offer must name a campaign template, not invent a promotion.
- reasoning must cite the client's actual numbers.
"""


def attach_client_features(callback_context: CallbackContext) -> None:
    callback_context.state["computed_facts"] = json.dumps(COMPUTED_FEATURES, indent=2)


root_agent = LlmAgent(
    name="segment",
    model=MODEL,
    instruction=INSTRUCTION,
    output_schema=SegmentDecision,
    output_key="decision",
    before_agent_callback=attach_client_features,
)
