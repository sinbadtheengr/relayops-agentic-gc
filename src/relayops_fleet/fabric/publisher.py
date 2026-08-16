"""Nightly fan-out: Cloud Scheduler → this job → one Pub/Sub message per client.

Runs as a Cloud Run Job. For each active clinic, selects clients eligible for
this wave and publishes a `CampaignRunMessage` per client, then exits. The
job does no model work itself — it is the fan-out, and it must stay cheap
enough to run over every tenant every night.

Caps are enforced HERE, not in the worker: once N messages are published the
spend is committed, so SEGMENT_MAX_CLIENTS is applied at publish time and the
run logs how many clients it deliberately did not enqueue.

See CLAUDE.md F-6.
"""
from __future__ import annotations

# TODO(F-6): implement publish_campaign_run(run_id, clinic_id, dry_run).
