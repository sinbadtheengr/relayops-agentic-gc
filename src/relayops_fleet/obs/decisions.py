"""The decision log — a product surface, not a debug table.

Every model call in this system writes an `agent_decisions` row BEFORE its
output is allowed to affect anything else. The approval dashboard links each
draft to the exact call that produced it, so the answer to "why did it say
that to my client?" is a row, not a shrug.

Two sinks, deliberately unequal:

1. **Postgres `agent_decisions`** — mandatory. If this write fails, the run
   fails. An unlogged decision is not allowed to reach a clinic.
2. **Cloud Logging** (`relayops-agent-decisions`) — best-effort. Useful for
   ops dashboards; never load-bearing.

Port target: relayops-prod `src/relayops/obs.py` — see CLAUDE.md F-10.
"""
from __future__ import annotations

from typing import Any


def log_agent_decision(
    *,
    agent_name: str,
    clinic_id: int,
    client_key: str | None,
    inputs: dict[str, Any],
    output: dict[str, Any] | None,
    reasoning: str,
    model: str,
    tokens: int,
    latency_ms: int,
    decided_by: str = "model",
    gate_reason: str | None = None,
) -> int:
    """Write the decision row and return its id. Raises if Postgres refuses.

    `decided_by='rule'` rows carry `model=''`, `tokens=0` and a `gate_reason`
    — they are the record of a client the system deliberately did NOT
    contact, which is the half of the audit trail a compliance review
    actually asks for.

    TODO(F-10): implement.
    """
    raise NotImplementedError("F-10")
