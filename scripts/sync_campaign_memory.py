"""Recompute each clinic's campaign memory and write it to Memory Bank.

    python scripts/sync_campaign_memory.py [--clinic-id N] [--dry-run]

Run after outcomes are recorded — nightly alongside the campaign run, or by
hand before a demo. Reading is cheap and happens on every draft; writing is
this script's job alone, so the agent path never mutates the store it reads.

**Replaces, never appends.** Facts are recomputed aggregates over the whole
outcome log, so appending would leave last month's "3 of 4 converted" sitting
beside this month's "5 of 9" as though both were currently true.

**Per clinic, always.** Every write carries the clinic's scope. A clinic with
nothing to say gets its memories cleared rather than keeping stale ones.

See CLAUDE.md F-9.3.
"""
from __future__ import annotations

import argparse
import asyncio

from relayops_fleet.agents.memory import (
    delete_clinic_memories,
    is_configured,
    replace_clinic_memories,
)
from relayops_fleet.config import get_settings
from relayops_fleet.core.campaign_memory import compose_facts
from relayops_fleet.db import campaign_repo, memory_repo, repo


async def sync(*, clinic_id: int | None = None, dry_run: bool = False) -> int:
    """Rewrite memory for one clinic or all active ones. Returns facts written."""
    settings = get_settings()
    if not is_configured():
        raise SystemExit(
            "AGENT_ENGINE_ID is not set. Point it at the Agent Engine instance "
            "hosting the Memory Bank (see .env.example)."
        )

    engine = repo.build_engine(settings.database_url)
    Session = repo.build_sessionmaker(engine)
    written = 0

    with Session() as session, session.begin():
        clinics = [
            c
            for c in campaign_repo.active_clinics(session)
            if clinic_id is None or c.id == clinic_id
        ]
        if not clinics:
            raise SystemExit(f"no active clinic matched clinic_id={clinic_id}")

        for clinic in clinics:
            results = memory_repo.clinic_segment_results(session, clinic_id=clinic.id)
            facts = compose_facts(results, window_days=settings.attribution_window_days)

            print(f"\n{clinic.name} (clinic_id={clinic.id}) — {len(facts)} fact(s)")
            for fact in facts:
                print(f"  - {fact}")
            if not facts:
                print("  (no contacted segment yet; memory will be cleared)")

            if dry_run:
                continue
            if facts:
                written += await replace_clinic_memories(clinic.id, facts)
            else:
                # Clearing is the correct end state, not a no-op: memory that
                # no longer follows from the outcome log must not keep
                # steering drafts.
                delete_clinic_memories(clinic.id)

    engine.dispose()
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic-id", type=int, default=None, help="restrict to one tenant")
    ap.add_argument(
        "--dry-run", action="store_true", help="print the facts without writing them"
    )
    args = ap.parse_args()
    count = asyncio.run(sync(clinic_id=args.clinic_id, dry_run=args.dry_run))
    print(f"\n{'would write' if args.dry_run else 'wrote'} {count} fact(s)")
