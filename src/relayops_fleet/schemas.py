"""Pydantic models for structured Gemini output and for the Pub/Sub envelope.

Strict models (extra='forbid') so response_schema validation rejects drift.
Ported from relayops-prod `relayops.schemas` — see PROJECT_OVERVIEW.md
§"Disclosed prior work".
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Agent outputs --------------------------------------------------------


class SegmentDecision(StrictModel):
    """The segmentation agent's targeting decision for one lapsed client.

    The model only ever produces this AFTER the deterministic gates in
    `core.gates` have passed. A gated client never reaches Gemini at all —
    it gets a `decided_by='rule'` decision row and costs nothing (F-4).
    """

    target: bool = Field(description="Whether to include this client in the win-back campaign")
    priority_tier: Literal["A", "B", "C"] = Field(
        description="A = contact first (high value, high win-back odds), B = standard, "
        "C = low priority or do not rush"
    )
    suggested_offer: str = Field(
        description="Which campaign template/incentive fits, e.g. 'Segment A we-miss-you, "
        "no incentive' or 'Segment C welcome-back credit'; VIPs never get discounts"
    )
    reasoning: str = Field(description="Grounded justification citing the client's actual numbers")


class OutreachDraftSet(StrictModel):
    """SMS + email drafts for one targeted client, generated from the campaign
    template that matches their segment. Merge fields like {{clinic_name}},
    {{booking_link}}, {{staff_name}} stay as placeholders for the clinic to
    fill; the client's first name and segment-appropriate incentive wording
    are written out.

    `core.casl.enforce_casl()` runs on every instance before persistence —
    the model is never trusted to remember the STOP line (F-5).
    """

    sms: str = Field(description="SMS draft; MUST end with the STOP opt-out line")
    email_subject: str = Field(description="Email subject line; specific, no clickbait, no ALL CAPS")
    email_body: str = Field(
        description="Email draft body. Ends with the sender-identification + unsubscribe "
        "footer. Never guarantees a medical result and never invents a promotion."
    )
    reasoning: str = Field(description="Which template section was used and why")


# --- Fabric envelope ------------------------------------------------------


class CampaignRunMessage(StrictModel):
    """One Pub/Sub message = one lapsed client's agent run (F-6).

    Carries the tenant explicitly. The worker NEVER infers the clinic from
    ambient state — a message that lost its clinic_id is dead-lettered, not
    guessed, because guessing wrong crosses a tenant boundary.
    """

    run_id: str = Field(description="Batch id shared by every message in one nightly run")
    clinic_id: int = Field(description="Tenant scope; all reads and writes are filtered by this")
    client_key: str = Field(description="E.164 phone — the per-clinic natural key")
    dry_run: bool = Field(default=True, description="When true, no Gemini call and no writes")
