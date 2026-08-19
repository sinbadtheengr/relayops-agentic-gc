"""Pub/Sub push handler: one message = one client's agent run.

Deployed as a Cloud Run service behind a Pub/Sub push subscription (F-6).

Delivery guarantees this must survive:

- **At-least-once delivery.** Pub/Sub will redeliver. Every write is
  idempotent on `(clinic_id, client_key, channel)`, so a redelivery updates a
  draft in place rather than producing a second one. A clinic seeing two
  identical drafts for one client loses trust in the whole queue.
- **Poison messages.** A message that fails deterministically is published to
  the dead-letter topic by this handler and then acked, rather than being
  nacked into a retry loop that bills for tokens on every pass.
- **Lost tenancy.** A message missing `clinic_id` is dead-lettered
  immediately. It is never inferred — a wrong guess writes one clinic's
  client into another's campaign.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import UTC, date, datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..agents.callbacks import AS_OF, CLIENT_ROW, VIP_CUTOFF_CENTS, screen_staff_note
from ..agents.outreach import run_outreach
from ..agents.segment import run_segment
from ..config import get_settings
from ..core.casl import apply_copy_guards
from ..core.features import compute_vip_cutoff_cents
from ..core.gates import apply_gates
from ..core.personalize import apply_merge_fields
from ..db import campaign_repo, consent_repo
from ..db.repo import build_engine, build_sessionmaker
from ..obs.decisions import log_agent_decision, log_gate_decision
from ..schemas import CampaignRunMessage

log = logging.getLogger(__name__)

app = FastAPI(title="RelayOps Fleet worker")

_engine = None
_Session = None


def _session_factory():
    global _engine, _Session
    if _Session is None:
        _engine = build_engine()
        _Session = build_sessionmaker(_engine)
    return _Session


class PermanentFailure(Exception):
    """This message will fail identically on every redelivery."""


def decode_envelope(envelope: dict[str, Any]) -> CampaignRunMessage:
    """Decode a Pub/Sub push envelope into a typed message.

    Raises PermanentFailure for anything malformed: a message we cannot parse
    will not become parseable on retry.
    """
    message = envelope.get("message")
    if not isinstance(message, dict):
        raise PermanentFailure("envelope has no 'message' object")
    raw = message.get("data")
    if not raw:
        raise PermanentFailure("message has no data")
    try:
        payload = base64.b64decode(raw)
    except (binascii.Error, ValueError) as exc:
        raise PermanentFailure(f"data is not valid base64: {exc}") from exc
    try:
        return CampaignRunMessage.model_validate_json(payload)
    except ValidationError as exc:
        # Covers the missing-clinic_id case: the field is required, so a
        # message without it fails validation and is dead-lettered rather
        # than having its tenant guessed.
        raise PermanentFailure(f"message does not match CampaignRunMessage: {exc}") from exc


def dead_letter(envelope: dict[str, Any], reason: str) -> None:
    """Publish a poison message to the DLQ ourselves, then ack it.

    Pub/Sub's own dead-lettering only fires after `max_delivery_attempts`,
    which means a deterministically-broken message is redelivered several
    times first. Publishing it here and acking gets it out of the way on the
    first pass, and — unlike simply acking — does not silently discard it.
    """
    settings = get_settings()
    try:
        from google.cloud import pubsub_v1

        publisher = pubsub_v1.PublisherClient()
        topic = publisher.topic_path(settings.google_cloud_project, settings.pubsub_dlq_topic)
        publisher.publish(
            topic,
            json.dumps({"reason": reason, "envelope": envelope}, default=str).encode("utf-8"),
        ).result()
    except Exception as exc:  # noqa: BLE001 — DLQ failure must not retry the poison message
        log.error("dead-letter publish failed (%s); message dropped: %s", exc, reason)


async def run_one_client(
    session: Session,
    *,
    run_id: str,
    clinic_id: int,
    client_key: str,
    as_of: date | None = None,
    dry_run: bool = True,
) -> str:
    """The per-client pipeline: gates → segment → outreach → persist.

    Returns a short outcome string for logging. Raises PermanentFailure when
    the message names something that does not exist.

    **Nothing is ever sent here.** Drafts land with status='draft'.
    """
    as_of = as_of or datetime.now(UTC).date()

    client = campaign_repo.get_client(session, clinic_id=clinic_id, client_key=client_key)
    if client is None:
        raise PermanentFailure(f"client {client_key} not found for clinic {clinic_id}")

    # 1. Gates — deterministic, no model, no spend.
    opted_out_phones, opted_out_emails = consent_repo.load_opt_outs(session)
    cooldown = consent_repo.recently_contacted_phones(session, clinic_id=clinic_id)
    gate = apply_gates(
        raw_phone=client.client_key,
        raw_email=client.email,
        last_visit=client.last_visit,
        opted_out_phones=opted_out_phones,
        opted_out_emails=opted_out_emails,
        recently_contacted_phones=cooldown,
    )
    if not gate.passed:
        decision = log_gate_decision(
            session,
            clinic_id=clinic_id,
            client_key=gate.client_key,
            gate_reason=gate.reason,
            inputs={"client_key": client_key, "as_of": as_of},
        )
        campaign_repo.record_client_decision(
            session,
            clinic_id=clinic_id,
            client_key=client_key,
            run_id=run_id,
            target=False,
            decided_by="rule",
            gate_reason=gate.reason,
            reasoning=f"gated: {gate.reason}",
            agent_decision_id=decision.id,
        )
        return f"gated:{gate.reason}"

    if dry_run:
        return "dry_run:would_segment"

    # 2. Authoritative features, computed per clinic.
    vip_cutoff = compute_vip_cutoff_cents(
        campaign_repo.clinic_spends(session, clinic_id=clinic_id)
    )
    state = {
        CLIENT_ROW: {
            "first_name": client.first_name,
            "last_visit": client.last_visit.isoformat(),
            "visit_count": client.visit_count,
            "lifetime_spend_cents": client.lifetime_spend_cents,
            "last_service": client.last_service,
            # Untrusted. Never reaches a prompt un-screened — see
            # agents.callbacks.screen_staff_note. Carried here rather than
            # omitted because omitting it made the whole F-9 screening layer
            # inert: every note reported "absent" and nothing was ever tested.
            "notes": client.notes,
        },
        VIP_CUTOFF_CENTS: vip_cutoff,
        AS_OF: as_of.isoformat(),
    }

    # 3. Segment.
    seg = await run_segment(dict(state))
    seg_decision = seg.output
    seg_row = log_agent_decision(
        session,
        agent_name="segment",
        clinic_id=clinic_id,
        client_key=client_key,
        inputs=state,
        output=seg_decision.model_dump(),
        reasoning=seg_decision.reasoning,
        model=seg.model,
        tokens=seg.tokens,
        latency_ms=seg.latency_ms,
    )
    campaign_repo.record_client_decision(
        session,
        clinic_id=clinic_id,
        client_key=client_key,
        run_id=run_id,
        target=seg_decision.target,
        decided_by="model",
        priority_tier=seg_decision.priority_tier,
        suggested_offer=seg_decision.suggested_offer,
        reasoning=seg_decision.reasoning,
        agent_decision_id=seg_row.id,
    )

    # 4. Not targeted → stop. No draft, no second model call.
    if not seg_decision.target:
        return "not_targeted"

    # 5. Outreach. The staff note is screened before it can reach the prompt;
    #    the verdict is recorded either way, so "the model never saw it" and
    #    "there was nothing to see" do not look identical in the audit trail.
    note, note_verdict = screen_staff_note(state)
    out = await run_outreach(dict(state))
    is_vip = bool(state[VIP_CUTOFF_CENTS]) and (client.lifetime_spend_cents or 0) >= vip_cutoff
    guarded = apply_copy_guards(out.output, is_vip=is_vip)

    # 6. Re-join the client's name locally. The agent never learned it — see
    #    core.personalize (GAP-014). Guards run first so they inspect the copy
    #    the model actually produced, not a name that happens to contain a
    #    word one of them looks for.
    personalized = apply_merge_fields(guarded.draft, first_name=client.first_name)

    out_row = log_agent_decision(
        session,
        agent_name="outreach",
        clinic_id=clinic_id,
        client_key=client_key,
        inputs={**state, "staff_note_verdict": note_verdict, "staff_note_used": bool(note)},
        output=personalized.model_dump(),
        reasoning=personalized.reasoning,
        model=out.model,
        tokens=out.tokens,
        latency_ms=out.latency_ms,
    )

    campaign_repo.upsert_draft(
        session,
        clinic_id=clinic_id,
        client_key=client_key,
        channel="sms",
        body=personalized.sms,
        needs_review=guarded.needs_review,
        agent_decision_id=out_row.id,
    )
    if client.email:
        campaign_repo.upsert_draft(
            session,
            clinic_id=clinic_id,
            client_key=client_key,
            channel="email",
            subject=personalized.email_subject,
            body=personalized.email_body,
            needs_review=guarded.needs_review,
            agent_decision_id=out_row.id,
        )
    return "drafted" + (" (needs review)" if guarded.needs_review else "")


@app.post("/")
async def handle_push(request: Request) -> Response:
    """Pub/Sub push endpoint.

    Status discipline — get this right or build an infinite billing loop:
      - 204 on success AND on permanent failure. A permanently-broken message
        is already dead-lettered and recorded; redelivering it would fail
        identically and spend tokens on every pass.
      - 500 ONLY on transient failure (database unreachable, model 503), so
        Pub/Sub retries with backoff.
    """
    envelope = await request.json()

    try:
        message = decode_envelope(envelope)
    except PermanentFailure as exc:
        dead_letter(envelope, str(exc))
        log.warning("dead-lettered malformed message: %s", exc)
        return Response(status_code=204)

    Session = _session_factory()
    try:
        with Session() as session, session.begin():
            outcome = await run_one_client(
                session,
                run_id=message.run_id,
                clinic_id=message.clinic_id,
                client_key=message.client_key,
                dry_run=message.dry_run,
            )
    except PermanentFailure as exc:
        dead_letter(envelope, str(exc))
        log.warning("dead-lettered unprocessable message: %s", exc)
        return Response(status_code=204)
    except Exception:
        # Transient by assumption. Let Pub/Sub retry with backoff rather than
        # losing a client's run to a momentary database or model outage.
        log.exception("transient failure; returning 500 for redelivery")
        return Response(status_code=500)

    log.info("run=%s clinic=%s outcome=%s", message.run_id, message.clinic_id, outcome)
    return Response(status_code=204)


# NOT /healthz: Cloud Run's frontend intercepts that path and returns its
# own HTML 404, so the route never reaches the app. Verified 2026-08-16.
@app.get("/health")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
