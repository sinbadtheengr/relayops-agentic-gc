"""Nightly fan-out: Cloud Scheduler → this job → one Pub/Sub message per client.

Runs as a Cloud Run Job. For each active clinic it selects eligible clients
and publishes a `CampaignRunMessage` per client, then exits. The job does no
model work itself — it is the fan-out, and it must stay cheap enough to run
over every tenant every night.

Caps are enforced HERE, not in the worker: once N messages are published the
spend is committed. `SEGMENT_MAX_CLIENTS` is applied at publish time and the
run reports how many clients it deliberately did not enqueue, because a cap
that silently truncates looks identical to a clinic with fewer lapsed clients.

See CLAUDE.md F-6.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from google.cloud import pubsub_v1
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.campaign_repo import active_clinics, eligible_clients
from ..schemas import CampaignRunMessage


@dataclass
class PublishReport:
    """What the run did, including what it deliberately did not do."""

    run_id: str
    as_of: date
    dry_run: bool
    published: int = 0
    skipped_by_cap: int = 0
    per_clinic: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"run {self.run_id} as_of={self.as_of} dry_run={self.dry_run}",
            f"  published: {self.published}",
        ]
        if self.skipped_by_cap:
            lines.append(
                f"  NOT enqueued (SEGMENT_MAX_CLIENTS cap): {self.skipped_by_cap} — "
                "raise the cap to include them"
            )
        for name, count in sorted(self.per_clinic.items()):
            lines.append(f"    {name}: {count}")
        return "\n".join(lines)


def new_run_id() -> str:
    """A run id that sorts by time and is unique per fan-out."""
    return f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"


def publish_campaign_run(
    session: Session,
    *,
    as_of: date | None = None,
    run_id: str | None = None,
    dry_run: bool | None = None,
    max_clients: int | None = None,
    clinic_ids: Sequence[int] | None = None,
    publisher: pubsub_v1.PublisherClient | None = None,
) -> PublishReport:
    """Fan out one message per eligible client across every active clinic.

    `dry_run` defaults to the DRY_RUN setting, which itself defaults to true:
    an uncapped live fan-out is the expensive mistake, so it must be asked for.

    `max_clients` overrides SEGMENT_MAX_CLIENTS for this run — Settings is a
    frozen dataclass on purpose, so a per-run cap belongs in the signature
    rather than in mutated global config.

    `clinic_ids` restricts the run to named tenants. Omitted, it means every
    active clinic, which is the nightly behaviour; supplied, it lets an
    operator re-run one clinic without touching the others.
    """
    settings = get_settings()
    as_of = as_of or datetime.now(UTC).date()
    run_id = run_id or new_run_id()
    dry_run = settings.dry_run if dry_run is None else dry_run

    report = PublishReport(run_id=run_id, as_of=as_of, dry_run=dry_run)
    remaining = settings.segment_max_clients if max_clients is None else max_clients

    topic_path = None
    if not dry_run:
        publisher = publisher or pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(
            settings.google_cloud_project, settings.pubsub_topic_campaign_run
        )

    wanted = set(clinic_ids) if clinic_ids is not None else None
    for clinic in active_clinics(session):
        if wanted is not None and clinic.id not in wanted:
            continue
        clients = eligible_clients(session, clinic_id=clinic.id, as_of=as_of)
        take = clients if remaining >= len(clients) else clients[: max(0, remaining)]
        report.skipped_by_cap += len(clients) - len(take)

        for client in take:
            message = CampaignRunMessage(
                run_id=run_id,
                clinic_id=clinic.id,
                client_key=client.client_key,
                dry_run=dry_run,
            )
            if not dry_run and publisher is not None and topic_path is not None:
                publisher.publish(topic_path, message.model_dump_json().encode("utf-8")).result()
            report.published += 1

        remaining -= len(take)
        if take:
            report.per_clinic[clinic.name] = len(take)

    return report


def _main() -> None:
    """Cloud Run Job entrypoint."""
    from ..db.repo import build_engine, build_sessionmaker

    engine = build_engine()
    Session = build_sessionmaker(engine)
    with Session() as session:
        report = publish_campaign_run(session)
    print(report.summary())
    print(json.dumps({"run_id": report.run_id, "published": report.published}))


if __name__ == "__main__":
    _main()
