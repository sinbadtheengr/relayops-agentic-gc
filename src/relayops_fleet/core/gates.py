"""Deterministic compliance gates. NO LLM CALLS IN THIS MODULE — ever.

This is the hard boundary the product is built on: a client who fails any gate
below is never sent to Gemini, never drafted for, and never contacted. The
model cannot see these decisions and cannot overturn them.

Port target: relayops-prod `src/relayops/consent.py` (e164, OptOutRegister,
load_opt_outs, recently_contacted_keys) — see CLAUDE.md F-4.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

GateReason = Literal[
    "invalid_phone",
    "opted_out",
    "suppressed",
    "cooldown",
    "no_last_visit",
    "passed",
]


@dataclass(frozen=True)
class GateResult:
    """Why a client did or did not reach the model.

    `passed=False` produces a `decided_by='rule'` row in `client_decisions`
    with `reason` recorded. Zero token spend. This is a feature, not an
    optimization: the audit trail must show WHY someone was not contacted.
    """

    passed: bool
    reason: GateReason


def apply_gates(
    *,
    raw_phone: str | None,
    last_visit: date | None,
    clinic_id: int,
    opted_out_keys: frozenset[str],
    suppressed_keys: frozenset[str],
    recently_contacted_keys: frozenset[str],
) -> GateResult:
    """Run every gate in order and return the first failure.

    Order matters and is deliberate:

    1. `invalid_phone` — an unparseable number cannot be contacted or keyed.
    2. `opted_out` — checked against the GLOBAL register, never per-clinic.
       Scoping opt-outs per clinic would permit contacting someone who opted
       out elsewhere. Under-suppressing is the compliance risk;
       over-suppressing only costs a lead.
    3. `suppressed` — the clinic's own do-not-contact upload.
    4. `cooldown` — per-clinic, driven by `contact_log` over
       CONTACT_COOLDOWN_DAYS. Scoped per clinic because a cooldown exists so
       ONE sender does not over-message someone.
    5. `no_last_visit` — a blank last-visit date makes everyone look maximally
       lapsed, so it is skipped with a reason rather than defaulted.

    TODO(F-4): implement. Port `e164()` and the register loaders from
    relayops-prod `src/relayops/consent.py:28-121`.
    """
    raise NotImplementedError("F-4")
