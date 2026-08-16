"""Envelope decoding and status discipline. No database, no network, no model.

The status rules are the part that can quietly cost real money: returning 500
for a permanently-broken message builds an infinite retry loop that spends
tokens on every pass.

Acceptance criteria for F-6 (see CLAUDE.md).
"""
from __future__ import annotations

import base64
import json

import pytest

from relayops_fleet.fabric.worker import PermanentFailure, decode_envelope
from relayops_fleet.schemas import CampaignRunMessage


def envelope(payload: str | bytes) -> dict:
    data = payload.encode() if isinstance(payload, str) else payload
    return {"message": {"data": base64.b64encode(data).decode(), "messageId": "1"}}


def test_valid_message_decodes() -> None:
    msg = CampaignRunMessage(run_id="r1", clinic_id=7, client_key="+14165550100", dry_run=False)
    decoded = decode_envelope(envelope(msg.model_dump_json()))
    assert decoded == msg


def test_message_without_clinic_id_is_permanently_failed() -> None:
    """Tenancy is never inferred.

    A wrong guess writes one clinic's client into another's campaign, so a
    message that lost its clinic_id must die rather than be repaired.
    """
    payload = json.dumps({"run_id": "r1", "client_key": "+14165550100"})
    with pytest.raises(PermanentFailure):
        decode_envelope(envelope(payload))


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"message": None},
        {"message": {}},
        {"message": {"data": ""}},
    ],
)
def test_malformed_envelopes_are_permanent_failures(bad: dict) -> None:
    with pytest.raises(PermanentFailure):
        decode_envelope(bad)


def test_non_base64_data_is_a_permanent_failure() -> None:
    with pytest.raises(PermanentFailure):
        decode_envelope({"message": {"data": "!!!not base64!!!"}})


def test_unknown_field_is_rejected() -> None:
    """CampaignRunMessage forbids extras: a message carrying a field this
    version does not understand is a version skew, not something to guess at."""
    payload = json.dumps(
        {
            "run_id": "r1",
            "clinic_id": 7,
            "client_key": "+1",
            "dry_run": True,
            "surprise": "value",
        }
    )
    with pytest.raises(PermanentFailure):
        decode_envelope(envelope(payload))


def test_dry_run_defaults_to_true_when_absent() -> None:
    """An uncapped live fan-out is the expensive mistake; live must be asked for."""
    payload = json.dumps({"run_id": "r1", "clinic_id": 7, "client_key": "+1"})
    assert decode_envelope(envelope(payload)).dry_run is True
