"""The decision log — a product surface, not a debug table.

Every model call in this system writes an `agent_decisions` row BEFORE its
output is allowed to affect anything else. The approval dashboard links each
draft to the exact call that produced it, so the answer to "why did it say
that to my client?" is a row, not a shrug.

Two sinks, deliberately unequal:

1. **Postgres `agent_decisions`** — mandatory. If this write fails, the run
   fails. An unlogged decision is not allowed to reach a clinic.
2. **Cloud Logging** (`relayops-agent-decisions`) — best-effort. Useful for
   ops dashboards; never load-bearing. A logging outage must not stop a
   clinic's campaign.

Ported from relayops-prod `src/relayops/obs.py` — see CLAUDE.md F-10.
"""
from __future__ import annotations

import json
import warnings
from functools import lru_cache
from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import AgentDecision

CLOUD_LOG_NAME = "relayops-agent-decisions"


@lru_cache
def _cloud_logger():
    """Lazy Cloud Logging client; None if ADC or the project is unavailable."""
    try:
        from google.cloud import logging as gcl

        client = gcl.Client(project=get_settings().google_cloud_project or None)
        return client.logger(CLOUD_LOG_NAME)
    except Exception as e:  # noqa: BLE001 — any auth/env failure degrades to local-only
        warnings.warn(
            f"Cloud Logging unavailable ({type(e).__name__}: {e}); "
            "decisions will only be written to Postgres.",
            stacklevel=2,
        )
        return None


def _jsonable(value: Any) -> Any:
    """Coerce to something JSONB accepts, stringifying what it must."""
    return json.loads(json.dumps(value, default=str))


def log_agent_decision(
    session: Session,
    *,
    agent_name: str,
    clinic_id: int,
    client_key: str | None,
    inputs: dict[str, Any],
    output: dict[str, Any] | None = None,
    reasoning: str = "",
    model: str = "",
    tokens: int = 0,
    latency_ms: int = 0,
    decided_by: str = "model",
    gate_reason: str | None = None,
) -> AgentDecision:
    """Write the decision row and return it. Raises if Postgres refuses.

    Takes a `session` rather than opening its own connection (a change from
    the relayops-prod signature): the row must land in the SAME transaction as
    the draft it explains, or a rollback could leave a draft whose decision
    row never existed.

    `decided_by='rule'` rows carry `model=''`, `tokens=0` and a `gate_reason` —
    they are the record of a client the system deliberately did NOT contact,
    which is the half of the audit trail a compliance review actually asks for.
    """
    row = AgentDecision(
        agent_name=agent_name,
        clinic_id=clinic_id,
        client_key=client_key,
        input=_jsonable(inputs),
        output=_jsonable(output) if output is not None else None,
        reasoning=reasoning,
        model=model,
        tokens=tokens,
        latency_ms=latency_ms,
        decided_by=decided_by,
        gate_reason=gate_reason,
    )
    session.add(row)
    # Flush, not commit: the caller owns the transaction boundary. Flushing
    # here means a constraint violation surfaces now, next to the code that
    # caused it, rather than at an unrelated commit later.
    session.flush()

    logger = _cloud_logger()
    if logger is not None:
        try:
            # NO CLIENT IDENTIFIER. `client_key` is an E.164 phone number, and
            # Cloud Logging is a broad ops sink with its own retention, access
            # rules and export paths — a second home for consumer PII that
            # nobody would think to audit.
            #
            # `decision_id` replaces it and loses nothing: it joins straight
            # back to the full row in our own Cloud SQL, so an operator
            # debugging a run has perfect correlation while the log itself
            # identifies no one. Chosen over hashing the phone because a bare
            # SHA of a 10-digit number is brute-forceable in seconds, and a
            # keyed hash would add secret management for no extra benefit.
            logger.log_struct(
                {
                    "agent": agent_name,
                    "clinic_id": clinic_id,
                    "decision_id": row.id,
                    "decided_by": decided_by,
                    "gate_reason": gate_reason,
                    "model": model,
                    "tokens": tokens,
                    "latency_ms": latency_ms,
                    "reasoning": reasoning,
                },
                severity="INFO",
            )
        except Exception as e:  # noqa: BLE001 — best-effort by design
            warnings.warn(f"Cloud Logging write failed: {e}", stacklevel=2)

    return row


def log_gate_decision(
    session: Session,
    *,
    clinic_id: int,
    client_key: str | None,
    gate_reason: str,
    inputs: dict[str, Any],
) -> AgentDecision:
    """Record a client the gates excluded. Zero tokens, no model.

    Exists as its own function because these rows are easy to forget: nothing
    downstream depends on them, so a missing one is invisible until someone
    asks why a client was never contacted and the system cannot say.
    """
    return log_agent_decision(
        session,
        agent_name="gates",
        clinic_id=clinic_id,
        client_key=client_key,
        inputs=inputs,
        output=None,
        reasoning=f"gated: {gate_reason}",
        decided_by="rule",
        gate_reason=gate_reason,
    )
