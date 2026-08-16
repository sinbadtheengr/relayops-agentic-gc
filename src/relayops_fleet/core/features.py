"""Deterministic feature computation. NO LLM CALLS IN THIS MODULE.

Everything the segment agent reasons over is computed here first: days lapsed
and its bucket, VIP status (80th-percentile spend WITHIN the clinic), visit
count, lifetime spend.

The model never does arithmetic. It receives finished numbers and is told they
are authoritative — the pattern proven in relayops-agentic-cine, where
`build_plan()` runs before the strategist agent speaks. A model that can
recompute a number can get it wrong, and this one's output becomes an invoice.

Ported from the feature half of relayops-prod
`src/relayops/pipeline/segment_agent.py:86` — see CLAUDE.md F-7.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date

# (bucket name, lower bound inclusive, upper bound exclusive) in days lapsed.
LAPSE_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("lapsed_90_180", 90, 180),
    ("lapsed_180_365", 180, 365),
    ("lapsed_365_plus", 365, 10**6),
)

VIP_PERCENTILE = 0.8

# Below this many spend figures, a "top 20%" is arithmetic without meaning: in
# a clinic with four known spends, one of them is the 80th percentile by
# construction. A cutoff of 0 disables VIP treatment rather than inventing a
# tier, and the segment agent simply sees is_vip=False.
MIN_SPENDS_FOR_VIP_CUTOFF = 5


@dataclass(frozen=True)
class ClientFeatures:
    """Authoritative facts about one lapsed client.

    Everything the model is allowed to reason over, and nothing it is allowed
    to recompute. `to_prompt_dict()` is what reaches the prompt.
    """

    first_name: str
    days_lapsed: int
    lapse_bucket: str | None
    visit_count: int | None
    lifetime_spend_cents: int | None
    is_vip: bool
    vip_cutoff_cents: int
    last_service: str | None

    def to_prompt_dict(self) -> dict[str, object]:
        return asdict(self)


def lapse_bucket(days_lapsed: int) -> str | None:
    """The campaign segment for this lapse, or None if not yet lapsed.

    None below 90 days is deliberate: a client seen last month is not a
    win-back target, and giving them a bucket would invite the model to
    campaign at someone who is simply a current client.
    """
    for name, low, high in LAPSE_BUCKETS:
        if low <= days_lapsed < high:
            return name
    return None


def compute_vip_cutoff_cents(
    spends: Sequence[int | None], *, percentile: float = VIP_PERCENTILE
) -> int:
    """The spend at `percentile` WITHIN one clinic. 0 means "no VIP tier".

    Per clinic, never across the book. A cross-tenant percentile would leak
    one clinic's price band into another's targeting, and would be meaningless
    for a clinic whose whole client list sits above another's VIP line.

    None spends are excluded rather than treated as 0 — an unreadable spend
    column is missing data, and counting it as zero would drag the cutoff down
    and manufacture VIPs.
    """
    known = sorted(s for s in spends if s is not None)
    if len(known) < MIN_SPENDS_FOR_VIP_CUTOFF:
        return 0
    # Nearest-rank: the smallest value with at least `percentile` of the data
    # at or below it. Chosen over interpolation because the cutoff is compared
    # with >=, and a real client's spend is a defensible threshold to quote to
    # a clinic owner asking "why is she a VIP?".
    index = max(0, min(len(known) - 1, round(percentile * len(known)) - 1))
    return known[index]


def compute_features(
    *,
    first_name: str,
    last_visit: date,
    as_of: date,
    visit_count: int | None,
    lifetime_spend_cents: int | None,
    vip_cutoff_cents: int,
    last_service: str | None = None,
) -> ClientFeatures:
    """Turn one client row into the facts the agent is given.

    `as_of` is passed in rather than read from the clock so a run is
    reproducible: re-running yesterday's batch must produce yesterday's
    numbers, and a decision row that cannot be recomputed cannot be defended.
    """
    days_lapsed = (as_of - last_visit).days
    is_vip = (
        lifetime_spend_cents is not None
        and vip_cutoff_cents > 0
        and lifetime_spend_cents >= vip_cutoff_cents
    )
    return ClientFeatures(
        first_name=first_name,
        days_lapsed=days_lapsed,
        lapse_bucket=lapse_bucket(days_lapsed),
        visit_count=visit_count,
        lifetime_spend_cents=lifetime_spend_cents,
        is_vip=is_vip,
        vip_cutoff_cents=vip_cutoff_cents,
        last_service=last_service,
    )
