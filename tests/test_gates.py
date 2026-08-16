"""Gate tests — the highest-value tests in the repo.

These run with no database, no network and no model. That is the point: the
compliance boundary is pure functions, so it is exhaustively testable and a
regression in it is impossible to miss.

Acceptance criteria for F-4 (see CLAUDE.md).
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from relayops_fleet.core.gates import GateResult, apply_gates, e164, normalize_email

LAST_VISIT = date(2026, 1, 15)
PHONE_RAW = "416-555-0142"
PHONE_E164 = "+14165550142"


def gate(**overrides) -> GateResult:
    kwargs = {"raw_phone": PHONE_RAW, "last_visit": LAST_VISIT}
    kwargs.update(overrides)
    return apply_gates(**kwargs)


# --- Normalization --------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["416-555-0142", "(416) 555-0142", "4165550142", " +1 416 555 0142 ", "+14165550142"],
)
def test_every_spelling_of_one_number_normalizes_identically(raw: str) -> None:
    """Two spellings of one number must not become two clients."""
    assert e164(raw) == PHONE_E164


@pytest.mark.parametrize("raw", [None, "", "   ", "not a phone", "123", "555-0142"])
def test_unusable_phones_normalize_to_none(raw: str | None) -> None:
    """None is the honest answer; the raw string would be an unreachable key."""
    assert e164(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Dana@Example.COM", "dana@example.com"), ("  a@b.ca ", "a@b.ca"), (None, None), ("", None)],
)
def test_email_matching_is_case_and_space_blind(raw: str | None, expected: str | None) -> None:
    assert normalize_email(raw) == expected


# --- Gate order and outcomes ---------------------------------------------


def test_clean_client_passes_and_carries_its_normalized_key() -> None:
    result = gate()
    assert result.passed
    assert result.reason == "passed"
    assert result.client_key == PHONE_E164


def test_unparseable_phone_is_skipped_with_reason() -> None:
    """No silent drop — every exclusion is recorded."""
    result = gate(raw_phone="not a phone")
    assert not result.passed
    assert result.reason == "invalid_phone"


def test_opted_out_client_is_gated() -> None:
    result = gate(opted_out_phones=frozenset({PHONE_E164}))
    assert not result.passed
    assert result.reason == "opted_out"


def test_opt_out_matches_on_email_when_phone_is_clean() -> None:
    """Email unsubscribes must suppress too, or the mechanism does not work."""
    result = gate(
        raw_email="Dana@Example.com", opted_out_emails=frozenset({"dana@example.com"})
    )
    assert not result.passed
    assert result.reason == "opted_out"


def test_suppressed_client_is_gated() -> None:
    result = gate(suppressed_phones=frozenset({PHONE_E164}))
    assert not result.passed
    assert result.reason == "suppressed"


def test_recently_contacted_client_is_gated() -> None:
    result = gate(recently_contacted_phones=frozenset({PHONE_E164}))
    assert not result.passed
    assert result.reason == "cooldown"


def test_blank_last_visit_is_skipped_not_defaulted() -> None:
    """A blank date must not make a client look maximally lapsed."""
    result = gate(last_visit=None)
    assert not result.passed
    assert result.reason == "no_last_visit"


def test_opt_out_outranks_cooldown_and_suppression() -> None:
    """When several gates would fire, the recorded reason must be the most
    serious one. 'cooldown' expires in 14 days; 'opted_out' never does, and an
    audit that logged the lesser reason would misrepresent why the person was
    excluded."""
    result = gate(
        opted_out_phones=frozenset({PHONE_E164}),
        suppressed_phones=frozenset({PHONE_E164}),
        recently_contacted_phones=frozenset({PHONE_E164}),
    )
    assert result.reason == "opted_out"


def test_an_invalid_phone_is_reported_before_anything_else() -> None:
    """A number that cannot be parsed cannot be matched against any register,
    so claiming 'opted_out' for it would be a guess."""
    result = gate(raw_phone="", opted_out_phones=frozenset({PHONE_E164}))
    assert result.reason == "invalid_phone"
    assert result.client_key is None


# --- Scoping: the rules that were real bugs elsewhere ---------------------


def test_opt_out_is_global_not_per_clinic() -> None:
    """A client who opted out at clinic A is gated at clinic B.

    The register is loaded globally, so the same frozenset reaches every
    clinic's run. Under-suppressing is the compliance risk; over-suppressing
    costs a lead.
    """
    global_opt_outs = frozenset({PHONE_E164})
    for _clinic in ("clinic A", "clinic B"):
        assert gate(opted_out_phones=global_opt_outs).reason == "opted_out"


def test_cooldown_is_per_clinic() -> None:
    """Clinic A contacting a shared client does not put clinic B into cooldown.

    Expressed as the caller passing a clinic-scoped set: clinic B's set does
    not contain the number, so clinic B's run proceeds.
    """
    contacted_by_a = frozenset({PHONE_E164})
    contacted_by_b: frozenset[str] = frozenset()

    assert gate(recently_contacted_phones=contacted_by_a).reason == "cooldown"
    assert gate(recently_contacted_phones=contacted_by_b).passed


def test_gated_client_never_reaches_the_model() -> None:
    """Structural guarantee: importing the gates cannot pull in an LLM SDK.

    Runs in a clean subprocess because the pytest session imports plenty of
    other modules — asserting against this process's sys.modules would prove
    nothing. Complements test_core_package_contains_no_llm_calls, which reads
    source text; this one checks the real runtime import graph.
    """
    probe = (
        "import sys; import relayops_fleet.core.gates; "
        "bad=[m for m in sys.modules if m.startswith(('google.genai','google.adk','vertexai'))]; "
        "print(bad)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.stdout.strip() == "[]", f"gates pulled in an LLM SDK: {result.stdout}"
