"""Print the decision log for a clinic -- the strongest shot in the demo.

The point of this view on camera is the token column: the rule-gated rows
cost zero, because the model was never asked whether those clients could be
contacted.

    python scripts/show_decisions.py
    python scripts/show_decisions.py --clinic "Glow Aesthetics (demo)"
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from relayops_fleet.config import get_settings
from relayops_fleet.db import repo

DEFAULT_CLINIC = "Glow Aesthetics (demo)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic", default=DEFAULT_CLINIC)
    args = ap.parse_args()

    engine = repo.build_engine(get_settings().database_url)
    with repo.unguarded(), engine.connect() as conn:
        clinic_id = conn.execute(
            text("SELECT id FROM clinics WHERE name = :n"), {"n": args.clinic}
        ).scalar()
        if clinic_id is None:
            print(f"No clinic named {args.clinic!r}.", file=sys.stderr)
            return 1

        rows = conn.execute(
            text(
                "SELECT agent_name, decided_by, coalesce(gate_reason, '-') AS gate, "
                "       tokens, latency_ms "
                "FROM agent_decisions WHERE clinic_id = :c ORDER BY id"
            ),
            {"c": clinic_id},
        ).all()

    print(f"\n  {args.clinic}  --  {len(rows)} decisions\n")
    print(f"  {'AGENT':<10} {'BY':<6} {'GATE REASON':<12} {'TOKENS':>8} {'LATENCY':>9}")
    print(f"  {'-' * 10} {'-' * 6} {'-' * 12} {'-' * 8} {'-' * 9}")
    for r in rows:
        latency = f"{r.latency_ms:,}ms" if r.latency_ms else "-"
        print(f"  {r.agent_name:<10} {r.decided_by:<6} {r.gate:<12} "
              f"{r.tokens:>8,} {latency:>9}")

    gated = [r for r in rows if r.decided_by == "rule"]
    billed = sum(r.tokens for r in rows)
    print(f"\n  {len(gated)} clients gated by rule, costing {sum(r.tokens for r in gated)} tokens.")
    print(f"  {billed:,} tokens spent in total.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
