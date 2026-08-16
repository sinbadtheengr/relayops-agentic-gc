"""Clinic export ingestion. NO LLM CALLS IN THIS MODULE.

Jane, Boulevard, Vagaro, Mindbody and Fresha all name the same six facts
differently and some split the name across two columns, so headers are
matched by synonym rather than hard-coded.

Nothing is silently defaulted, because segmentation targets on these fields:
a blanked spend makes a VIP look ordinary and a blanked date makes everyone
look maximally lapsed. A row missing a name, phone or readable last-visit
date is **skipped with a reason**; unreadable counts and spend become None,
not 0. A missing required column raises rather than guessing.

Slashed dates are disambiguated from the column itself (a value like `25/12`
can only be d/m/y) and flagged in the report when the file never resolves it.

The skip report prints on every run — rows it skipped are clients who will
never be contacted, and silence there would look like a clean import.

Port target: relayops-prod `src/relayops/pipeline/client_import.py` — see
CLAUDE.md F-3.
"""
from __future__ import annotations

# TODO(F-3): port, then add the GCS-triggered entrypoint (Eventarc → Cloud
# Run) so a clinic dropping an export in their bucket starts a run.
