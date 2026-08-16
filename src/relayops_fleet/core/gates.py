"""Deterministic compliance gates. NO LLM CALLS IN THIS MODULE — ever.

This is the hard boundary the product is built on: a client who fails any gate
below is never sent to Gemini, never drafted for, and never contacted. The
model cannot see these decisions and cannot overturn them.

Everything here is a pure function over values the caller has already loaded.
No database, no clock reads, no network — so the compliance boundary is
exhaustively testable and a regression in it is impossible to miss.

Ported from relayops-prod `src/relayops/consent.py:28-121` — see CLAUDE.md F-4.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import phonenumbers

GateReason = Literal[
    "invalid_phone",
    "opted_out",
    "suppressed",
    "cooldown",
    "no_last_visit",
    "passed",
]

# Clinic exports are Canadian (GTA med spas). A bare 10-digit number is parsed
# against this region; anything already in +E.164 keeps its own country code.
DEFAULT_REGION = "CA"


def e164(raw: str | None) -> str | None:
    """Normalize a phone to E.164, or None if it is not a valid number.

    None is the honest answer for unparseable input. Returning the raw string
    would let an unreachable number through as a `client_key`, and two
    spellings of the same number would become two clients.
    """
    if not raw or not str(raw).strip():
        return None
    try:
        parsed = phonenumbers.parse(str(raw), DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_email(raw: str | None) -> str | None:
    """Lowercase and strip, or None. Matching an opt-out must be case-blind."""
    if not raw or not str(raw).strip():
        return None
    return str(raw).strip().lower()


@dataclass(frozen=True)
class GateResult:
    """Why a client did or did not reach the model.

    `passed=False` produces a `decided_by='rule'` row in `client_decisions`
    with `reason` recorded. Zero token spend. This is a feature, not an
    optimization: the audit trail must show WHY someone was not contacted.

    `client_key` carries the normalized phone when one could be parsed, so the
    caller never re-derives it (and never writes an unnormalized key).
    """

    passed: bool
    reason: GateReason
    client_key: str | None = None


def apply_gates(
    *,
    raw_phone: str | None,
    raw_email: str | None = None,
    last_visit: date | None,
    opted_out_phones: frozenset[str] = frozenset(),
    opted_out_emails: frozenset[str] = frozenset(),
    suppressed_phones: frozenset[str] = frozenset(),
    recently_contacted_phones: frozenset[str] = frozenset(),
) -> GateResult:
    """Run every gate in order and return the first failure.

    Order matters and is deliberate:

    1. `invalid_phone` — an unparseable number cannot be contacted or keyed.
    2. `opted_out` — checked against the GLOBAL register, never per-clinic.
       Scoping opt-outs per clinic would permit contacting someone who opted
       out elsewhere. Under-suppressing is the compliance risk;
       over-suppressing only costs a lead. Matched on phone OR email, because
       SMS opts out by STOP and email opts out by unsubscribe link.
    3. `suppressed` — the clinic's own do-not-contact upload.
    4. `cooldown` — per clinic. A cooldown exists so ONE sender does not
       over-message someone; `recently_contacted_phones` MUST already be
       scoped to the calling clinic.
    5. `no_last_visit` — a blank last-visit date makes everyone look maximally
       lapsed, so it is skipped with a reason rather than defaulted.

    Note there is no `clinic_id` parameter. Scoping is the caller's job and
    happens when the sets are loaded; taking a clinic_id here would imply this
    function does the scoping, which it does not.
    """
    phone = e164(raw_phone)
    if phone is None:
        return GateResult(passed=False, reason="invalid_phone")

    email = normalize_email(raw_email)
    if phone in opted_out_phones or (email is not None and email in opted_out_emails):
        return GateResult(passed=False, reason="opted_out", client_key=phone)

    if phone in suppressed_phones:
        return GateResult(passed=False, reason="suppressed", client_key=phone)

    if phone in recently_contacted_phones:
        return GateResult(passed=False, reason="cooldown", client_key=phone)

    if last_visit is None:
        return GateResult(passed=False, reason="no_last_visit", client_key=phone)

    return GateResult(passed=True, reason="passed", client_key=phone)
