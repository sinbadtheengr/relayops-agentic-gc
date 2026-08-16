"""SQLAlchemy models. Every Track-2 table is tenant-scoped by `clinic_id`.

THE HARD RULE, inherited from relayops-prod and non-negotiable here:
**Track 1 (prospect businesses we sell to) and Track 2 (a signed clinic's
customers — consumer PII) never join.** This repo contains Track 2 only. No
model here carries a `prospect_id`, and no query in this repo reads a
prospects table. If a future feature seems to need the join, it is the
feature that is wrong.

See CLAUDE.md F-2 for the migration set.
"""
from __future__ import annotations

# TODO(F-2): declare models + Alembic migrations 0001-0006:
#
#   clinics            tenant registry; get_clinic() refuses to create on a
#                      miss, so a typo cannot silently split one clinic's
#                      data across two tenants.
#   clients            per-clinic lapsed client rows; UNIQUE (clinic_id, client_key)
#   client_decisions   one row per client per run; decided_by in ('rule','model'),
#                      gate_reason nullable, FK -> agent_decisions
#   outreach_drafts    UNIQUE (clinic_id, client_key, channel); status in
#                      ('draft','approved','rejected','sent'); approved drafts
#                      are never overwritten by a re-run
#   contact_log        per-clinic; drives the cooldown gate
#   opt_outs           GLOBAL, not clinic-scoped — see core/gates.py
#   outreach_outcomes  append-only event log; booked/no_show/showed. NOT a
#                      status column: a client can book, no-show, then rebook
#                      and attend, and a billing dispute turns on exactly
#                      that history.
#   agent_decisions    id, ts, agent_name, clinic_id, input jsonb, output jsonb,
#                      reasoning, model, tokens, latency_ms
