"""Pub/Sub push handler: one message = one client's agent run.

Deployed as a Cloud Run service behind a Pub/Sub push subscription (F-6).
The nightly Cloud Scheduler job publishes one message per eligible client;
this handler runs the agent pipeline for exactly that client.

Delivery guarantees this must survive:

- **At-least-once delivery.** Pub/Sub will redeliver. Every write is
  idempotent on `(clinic_id, client_key, channel)`, so a redelivery updates
  a draft in place rather than producing a second one. A clinic seeing two
  identical drafts for one client loses trust in the whole queue.
- **Poison messages.** A message that fails deterministically is
  dead-lettered to PUBSUB_DLQ_TOPIC after the configured retry count, never
  retried forever.
- **Lost tenancy.** A message missing `clinic_id` is dead-lettered
  immediately. It is never inferred — a wrong guess writes one clinic's
  client into another's campaign.
"""
from __future__ import annotations

from typing import Any


def handle_push(envelope: dict[str, Any]) -> tuple[str, int]:
    """Decode a Pub/Sub push envelope and run one client through the fleet.

    Returns (body, status). Status discipline:
      - 204 on success AND on permanent failure (ack — do not redeliver a
        message that will fail identically next time; it is already in the
        decision log with its reason).
      - 500 only on transient failure (DB unreachable, model 503) so Pub/Sub
        retries with backoff.

    Returning 500 for a permanent failure is the classic way to build an
    infinite retry loop that bills for tokens on every pass.

    TODO(F-6): implement.
    """
    raise NotImplementedError("F-6")


def run_one_client(*, run_id: str, clinic_id: int, client_key: str, dry_run: bool) -> None:
    """The per-client pipeline: gates → segment → outreach → persist.

    1. Load the client scoped to `clinic_id`.
    2. `core.gates.apply_gates()` — on failure, write a `decided_by='rule'`
       decision with the reason and RETURN. No model call.
    3. Segment agent (Gemini, structured `SegmentDecision`).
    4. If `target=False`, persist the decision and return — no draft.
    5. Outreach agent (Gemini, structured `OutreachDraftSet`).
    6. `core.casl.enforce_casl()` + the copy guards.
    7. Persist draft with `status='draft'`. **Nothing is ever sent here.**

    TODO(F-6): implement.
    """
    raise NotImplementedError("F-6")
