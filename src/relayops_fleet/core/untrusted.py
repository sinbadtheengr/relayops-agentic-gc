"""Deterministic screening of clinic-supplied free text. NO LLM CALLS.

A clinic's export carries a `notes` column written by front-desk staff and
sometimes transcribed from clients. It is genuinely useful context — "prefers
afternoons", "sensitive skin, patch test first" — and it is also the one
string in this system that an outsider can influence and that reaches a
prompt.

This module is the layer that always runs. Model Armor (`agents/armor.py`) is
a second, better screen, but it is a network call to a remote service, and a
compliance boundary that fails open when a service is down is not a boundary.

The rule for both layers: **suspicious notes are dropped, not sanitized.**
Rewriting an attacker's text and then trusting the rewrite is a strictly
worse position than proceeding without the field. Notes are optional colour;
losing them costs personalization, never correctness.

See CLAUDE.md F-9.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Phrases that attempt to redirect the model rather than describe a client.
# Deliberately narrow: this runs on real staff notes, and a screen that eats
# ordinary clinic shorthand would quietly strip useful context on every run.
_INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)\b", "override attempt"),
    (r"\bdisregard\s+(all\s+|any\s+)?(previous|prior|above|the)\b", "override attempt"),
    (r"\b(system|developer)\s+(prompt|instruction|message)\b", "prompt reference"),
    (r"\byou\s+are\s+now\b", "role reassignment"),
    (r"\bact\s+as\s+(a|an|the)\b", "role reassignment"),
    (r"\bnew\s+instructions?\b", "override attempt"),
    (r"\boverride\s+(the\s+)?(rules?|instructions?|policy)\b", "override attempt"),
    # An instruction to offer money off is the specific harm this system must
    # not commit: it would put an unauthorised discount in a clinic's name.
    (r"\boffer\s+\w*\s*\d{1,3}\s?%\s*off\b", "unauthorised offer"),
    (r"\bgive\s+(everyone|them|all)\b.{0,20}\bfree\b", "unauthorised offer"),
)

_COMPILED = tuple((re.compile(p, re.IGNORECASE), reason) for p, reason in _INJECTION_PATTERNS)

# Text long enough to hide an instruction in is not a clinic note.
MAX_NOTE_CHARS = 400


@dataclass(frozen=True)
class ScreenResult:
    """Whether a note may reach a prompt, and why not if it may not."""

    safe: bool
    reason: str | None = None

    @property
    def verdict(self) -> str:
        """Short label recorded on the decision row."""
        return "clean" if self.safe else f"blocked:{self.reason}"


def screen_note(text: str | None) -> ScreenResult:
    """Deterministic pre-screen. Runs on every note, always, offline.

    Returns safe=True for empty input: there is nothing to screen and nothing
    to include, which is not a failure.
    """
    if text is None or not text.strip():
        return ScreenResult(safe=True)

    if len(text) > MAX_NOTE_CHARS:
        return ScreenResult(safe=False, reason="over_length")

    for pattern, reason in _COMPILED:
        if pattern.search(text):
            return ScreenResult(safe=False, reason=reason.replace(" ", "_"))

    return ScreenResult(safe=True)
