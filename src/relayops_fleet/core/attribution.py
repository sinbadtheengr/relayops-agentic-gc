"""Billing. Attribution is COMPUTED, never stored.

The pricing model is "$50 per client who books and shows up — each client
counts once — capped at $1,500". This module decides what a clinic owes,
which makes it the one output someone will argue with. It is built for that
argument.

A show bills because a logged contact preceded it inside
ATTRIBUTION_WINDOW_DAYS. Recompute it and the same evidence gives the same
answer, whereas a stored flag would drift the moment a contact was corrected.

Every billable line names the contact that earned it and the gap in days, and
**excluded outcomes are shown with reasons** rather than filtered away: a
clinic seeing eight shows and a bill for three needs to know why five did not
count.

Not billed: a show with no contact behind it (they were coming anyway), a
contact logged AFTER the appointment, a gap outside the window, anything that
is not a show, and a second visit from a client already billed this period.

Port target: relayops-prod `src/relayops/attribution.py` — see CLAUDE.md F-11.
"""
from __future__ import annotations

# TODO(F-11): port. Keep the "per client, once" rule — the sales script used
# to say both "per client who actually shows up" and "per booked-and-showed
# appointment", which is one fee versus three for a client who rebooks. The
# code bills per client and the copy must match it.
