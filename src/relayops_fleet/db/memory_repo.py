"""Aggregating what converted, per clinic, for campaign memory.

`core.campaign_memory` is pure so the phrasing can be argued with in a test
rather than against a database. This module is the only place that knows
where its inputs live — the same split as `billing_repo` and for the same
reason.

**Conversion means exactly what billing means.** The converted set comes from
`core.attribution` via `billing_repo.billing_summary`, not from a second query
that counts shows its own way. If memory and the invoice could disagree about
what worked, one of them would be teaching the agent something the clinic was
never charged for.

See CLAUDE.md F-9.3.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.campaign_memory import CHANNELS, SegmentResult
from ..core.features import compute_features
from .billing_repo import billing_summary, load_contacts
from .models import AgentDecision

# Keys inside agent_decisions.input, which is the outreach agent's session
# state as it stood when the draft was written. Read from there rather than
# from `clients`: a client's last_visit changes the moment they come back, so
# today's row cannot say which segment they were in when they were contacted.
_CLIENT_ROW = "client_row"
_VIP_CUTOFF = "vip_cutoff_cents"
_AS_OF = "as_of"


def _segment_by_client(session: Session, *, clinic_id: int) -> dict[str, tuple[str, bool]]:
    """`client_key` -> (lapse_bucket, is_vip) as computed at draft time.

    The latest outreach decision wins. Rows whose stored state cannot be
    recomputed are skipped rather than guessed at: a segment we cannot
    reconstruct is one we must not teach the agent about.
    """
    rows = session.execute(
        select(AgentDecision.client_key, AgentDecision.input)
        .where(
            AgentDecision.clinic_id == clinic_id,
            AgentDecision.agent_name == "outreach",
        )
        .order_by(AgentDecision.ts, AgentDecision.id)
    ).all()

    segments: dict[str, tuple[str, bool]] = {}
    for client_key, state in rows:
        if not client_key or not isinstance(state, dict):
            continue
        client_row = state.get(_CLIENT_ROW)
        if not isinstance(client_row, dict):
            continue
        try:
            features = compute_features(
                last_visit=date.fromisoformat(client_row["last_visit"]),
                as_of=date.fromisoformat(state[_AS_OF]),
                visit_count=client_row.get("visit_count"),
                lifetime_spend_cents=client_row.get("lifetime_spend_cents"),
                vip_cutoff_cents=int(state[_VIP_CUTOFF]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if features.lapse_bucket is None:
            continue
        segments[client_key] = (features.lapse_bucket, features.is_vip)
    return segments


def clinic_segment_results(
    session: Session, *, clinic_id: int, since: date | None = None, until: date | None = None
) -> list[SegmentResult]:
    """What each (bucket, VIP, channel) segment achieved at this clinic.

    Counts contacts, not drafts. A draft nobody sent teaches nothing about
    what converts, and counting it would quietly dilute every rate with copy
    no client ever read.

    A contact whose segment cannot be reconstructed — a client contacted
    outside the agent pipeline, say — is dropped. It is a real contact, but
    attributing it to a template section we did not choose would put a number
    behind a claim we cannot support.
    """
    segments = _segment_by_client(session, clinic_id=clinic_id)
    if not segments:
        return []

    summary = billing_summary(session, clinic_id=clinic_id, since=since, until=until)
    # (client_key, channel) — the billable line names the contact that earned
    # it, so the channel that converted is known rather than assumed.
    converted = {(show.client_key, show.channel) for show in summary.billable}

    contacted_counts: dict[tuple[str, bool, str], int] = defaultdict(int)
    converted_counts: dict[tuple[str, bool, str], int] = defaultdict(int)
    counted_conversions: set[tuple[str, str]] = set()

    for contact in load_contacts(session, clinic_id=clinic_id):
        segment = segments.get(contact.client_key)
        if segment is None or contact.channel not in CHANNELS:
            continue
        key = (segment[0], segment[1], contact.channel)
        contacted_counts[key] += 1
        # Attribution bills a client once; count the conversion once too, or a
        # client contacted twice before returning would convert twice here and
        # a rate could exceed 100%.
        pair = (contact.client_key, contact.channel)
        if pair in converted and pair not in counted_conversions:
            counted_conversions.add(pair)
            converted_counts[key] += 1

    return [
        SegmentResult(
            lapse_bucket=bucket,
            is_vip=is_vip,
            channel=channel,
            contacted=contacted,
            converted=converted_counts[(bucket, is_vip, channel)],
        )
        for (bucket, is_vip, channel), contacted in sorted(contacted_counts.items())
    ]
