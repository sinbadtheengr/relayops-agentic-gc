"""F-1 step 3 — proof that a qualifying model returns a strictly-valid SegmentDecision.

This is the qualification evidence for GAP-001. Run it before building anything
on top of a model ID:

    PYTHONPATH=src python scripts/spike_f1.py

It makes one real Vertex AI call per candidate model with
`response_schema=SegmentDecision` (extra='forbid'), so a model that drifts by
even one extra key fails loudly here rather than in the fan-out.
"""
from __future__ import annotations

import json
import os
import time

from google import genai
from google.genai import types

from relayops_fleet.schemas import SegmentDecision

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "relayops-prod")

# MUST be "global". Verified 2026-08-15: every Gemini >=3.5 model is listed by
# models.list() in us-central1 but 404s on generate_content there — they are
# served only on the global endpoint. gemini-2.5-* still works regionally,
# which is why relayops-prod has never hit this. See GAP-001 / GAP-014.
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

# No Pro-tier model at >=3.5 exists on any accessible endpoint, so the tiering
# is newest-flash for the judgement call, older-flash for high-volume copy.
CANDIDATES = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]

# The authoritative-facts pattern (F-7): Python computes, the model interprets.
FEATURES = {
    "first_name": "Dana",
    "days_lapsed": 231,
    "lapse_bucket": "9-12 months",
    "visit_count": 7,
    "lifetime_spend_cents": 412000,
    "is_vip": True,
    "vip_threshold_cents": 280000,
    "last_service": "injectables",
    "clinic_name": "Demo Aesthetics (synthetic)",
}

SYSTEM = """You segment lapsed clients for a med spa's win-back campaign.

The numbers you are given are AUTHORITATIVE. They were computed by the system.
Do not recompute, contradict, or estimate them.

Rules you must follow:
- VIP clients never receive discount offers. They are an 80th-percentile
  spender; discounting trains them to wait for the discount.
- Never promise a medical result.
- suggested_offer must name a campaign template, not invent a promotion.
- reasoning must cite the client's actual numbers.
"""


def run(model: str) -> tuple[bool, str]:
    client = genai.Client(project=PROJECT, location=LOCATION, vertexai=True)
    started = time.monotonic()
    resp = client.models.generate_content(
        model=model,
        contents=json.dumps(FEATURES),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            response_mime_type="application/json",
            response_schema=SegmentDecision,
            temperature=0,
        ),
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    # Strict parse — extra='forbid' means drift raises here.
    decision = SegmentDecision.model_validate_json(resp.text)

    usage = resp.usage_metadata
    tokens = getattr(usage, "total_token_count", 0) if usage else 0
    print(f"  latency={latency_ms}ms tokens={tokens}")
    print(f"  target={decision.target} tier={decision.priority_tier}")
    print(f"  offer={decision.suggested_offer}")
    print(f"  reasoning={decision.reasoning[:160]}")

    # The one rule worth asserting in a spike: VIP must not be discounted.
    #
    # NOTE for F-5: a naive substring test for "discount" flags the compliant
    # phrase "non-discount perk". gemini-3.6-flash produced exactly that on the
    # first run of this spike and was wrongly marked a failure. The real guard
    # in core/casl.py must exclude negated forms, or it will bury reviewers in
    # false NEEDS REVIEW badges on correct copy.
    offer = decision.suggested_offer.lower()
    for negation in ("non-discount", "no discount", "without discount", "no incentive"):
        offer = offer.replace(negation, "")
    discounted = any(w in offer for w in ("discount", "% off", "percent off"))
    if FEATURES["is_vip"] and discounted:
        return False, "VIP was offered a discount"
    return True, "ok"


def main() -> None:
    print(f"project={PROJECT} location={LOCATION}\n")
    results: dict[str, str] = {}
    for model in CANDIDATES:
        print(f"--- {model}")
        try:
            ok, note = run(model)
            results[model] = "PASS" if ok else f"FAIL ({note})"
        except Exception as e:  # noqa: BLE001
            results[model] = f"ERROR {type(e).__name__}: {str(e)[:120]}"
        print()

    print("=" * 60)
    for model, verdict in results.items():
        print(f"{model:22} {verdict}")


if __name__ == "__main__":
    main()
