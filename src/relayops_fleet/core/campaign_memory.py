"""What converted, composed as text. NO LLM CALLS IN THIS MODULE.

Campaign memory answers one question for the outreach agent: *at this clinic,
which approved template section has actually brought people back?* The answer
is arithmetic over the append-only outcome log, phrased in Python.

**Nothing a model wrote ever reaches this module.** A memory is written once
and then injected into every later prompt, which makes model-authored text in
a memory a stored prompt injection with an indefinite blast radius — it would
outlive the run that produced it, the client it concerned, and the person who
could have recognised it. `compose_fact` therefore takes a typed record of
enumerated values and two integers, and there is no code path from an agent's
output into a fact.

**Facts are aggregates, never per-client.** The unit is (lapse bucket, VIP,
channel). No client is named or made identifiable — GAP-014's reasoning
applied to a store that outlives the run that created it.

See CLAUDE.md F-9.3.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .features import LAPSE_BUCKETS
from .templates import SEGMENT_SECTION, VIP_SECTION

# Human phrasing for each bucket. Checked against LAPSE_BUCKETS at import
# rather than written twice and hoped over: a bucket added there without a
# label here fails loudly instead of producing a memory that says "unknown".
BUCKET_PHRASE = {
    "lapsed_90_180": "lapsed 90 to 180 days",
    "lapsed_180_365": "lapsed 180 to 365 days",
    "lapsed_365_plus": "lapsed over a year",
}

# Below this many contacts, a conversion "rate" is a number without a finding
# behind it: one client returning is not a 100% rate. Same reasoning as
# MIN_SPENDS_FOR_VIP_CUTOFF in features.py — the fact still states its raw
# counts, and says plainly that they are too few to generalise from.
MIN_CONTACTS_FOR_RATE = 3

CHANNELS = ("sms", "email")

# Second layer under the structural guarantee. The composer cannot emit an
# identifier because it is never given one, but a fact is the one thing here
# that outlives its run, so it is checked before it is stored.
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d().\-\s]{6,}\d")


class MemoryFactRejected(ValueError):
    """A composed fact carried something that must not be stored."""


@dataclass(frozen=True)
class SegmentResult:
    """One clinic's outcome for one (bucket, VIP, channel) segment.

    `contacted` counts clients actually contacted — a draft nobody sent
    teaches nothing. `converted` counts attributed shows, using exactly the
    definition `core.attribution` bills on, so memory and invoice can never
    disagree about what worked.
    """

    lapse_bucket: str
    is_vip: bool
    channel: str
    contacted: int
    converted: int

    def __post_init__(self) -> None:
        if self.lapse_bucket not in BUCKET_PHRASE:
            raise MemoryFactRejected(f"unknown lapse bucket {self.lapse_bucket!r}")
        if self.channel not in CHANNELS:
            raise MemoryFactRejected(f"unknown channel {self.channel!r}")
        if self.contacted < 0 or self.converted < 0:
            raise MemoryFactRejected("counts cannot be negative")
        if self.converted > self.contacted:
            raise MemoryFactRejected(
                f"{self.converted} converted from {self.contacted} contacted"
            )

    @property
    def section(self) -> str:
        """The approved template section this segment was written from.

        VIP wins over the bucket, matching `templates.load_template_section` —
        if the two disagreed, memory would describe copy that was never sent.
        """
        header = VIP_SECTION if self.is_vip else SEGMENT_SECTION[self.lapse_bucket]
        return header.removeprefix("## ")

    @property
    def sort_key(self) -> tuple[str, bool, str]:
        return (self.lapse_bucket, self.is_vip, self.channel)


def assert_deidentified(fact: str) -> str:
    """Return `fact` if it names nobody; raise if it might.

    Belt and braces over a composer that is never handed an identifier. The
    cost of being wrong is a phone number sitting in a managed store outside
    Postgres, which is precisely what GAP-014 was about.
    """
    if _EMAIL.search(fact):
        raise MemoryFactRejected(f"fact contains an email address: {fact!r}")
    if _PHONE.search(fact):
        raise MemoryFactRejected(f"fact contains something phone-shaped: {fact!r}")
    return fact


def compose_fact(result: SegmentResult, *, window_days: int) -> str:
    """One clinic-scoped, de-identified sentence about what converted.

    Takes a typed record, never free text. See the module docstring for why
    that signature is the security property rather than a convenience.
    """
    tier = "VIP" if result.is_vip else "standard-tier"
    channel = "SMS" if result.channel == "sms" else "email"
    head = (
        f"{result.section} copy sent by {channel} to {tier} clients "
        f"{BUCKET_PHRASE[result.lapse_bucket]}: "
        f"{result.contacted} contacted, {result.converted} booked and showed "
        f"within {window_days} days"
    )
    if result.contacted < MIN_CONTACTS_FOR_RATE:
        return assert_deidentified(f"{head} (too few contacts to draw a rule from).")
    rate = round(100 * result.converted / result.contacted)
    return assert_deidentified(f"{head} ({rate}% of those contacted).")


def compose_facts(results: Sequence[SegmentResult], *, window_days: int) -> list[str]:
    """Every segment with at least one contact, in a stable order.

    A segment nobody was contacted in is dropped rather than stored as a zero:
    "0 contacted, 0 converted" reads as a campaign that failed when it means
    no campaign happened.
    """
    return [
        compose_fact(r, window_days=window_days)
        for r in sorted((r for r in results if r.contacted > 0), key=lambda r: r.sort_key)
    ]


def render_memory_block(facts: Sequence[str]) -> str:
    """The prompt section, or "" when there is nothing worth saying.

    The framing is doing real work. Memory is the only part of the prompt
    derived from past *outcomes* rather than from the clinic's approved copy,
    so it is labelled advisory and explicitly denied authority over the offer
    — otherwise "20% off converted last month" reads to a model as permission
    to offer 20% off to someone whose approved section has no discount in it.
    """
    if not facts:
        return ""
    lines = [
        "What has converted at this clinic before (from this clinic's own",
        "outcome log, aggregated - no individual client):",
    ]
    lines += [f"- {fact}" for fact in facts]
    lines += [
        "",
        "Use this for TONE and EMPHASIS only. It does not authorise an offer:",
        "the approved template section above remains the only source of what",
        "may be offered, and a VIP still gets no discount whatever converted",
        "for anyone else.",
    ]
    return "\n".join(lines)


# Import-time rather than a test, so the tables cannot drift even briefly.
_missing = {name for name, _, _ in LAPSE_BUCKETS} - set(BUCKET_PHRASE)
if _missing:  # pragma: no cover - a source edit, not a runtime state
    raise RuntimeError(f"lapse buckets without a memory phrase: {sorted(_missing)}")
