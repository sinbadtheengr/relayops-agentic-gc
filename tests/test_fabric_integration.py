"""Fan-out and idempotent writes against a real Postgres.

No model calls: the worker is exercised in dry-run and through the gate path,
which is where the replay-safety guarantees actually live.

    RELAYOPS_TEST_DB=1 python -m pytest tests/test_fabric_integration.py

Acceptance criteria for F-6 (see CLAUDE.md).
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from relayops_fleet.config import get_settings
from relayops_fleet.db import campaign_repo, consent_repo, repo
from relayops_fleet.db.models import Client, Clinic, OutreachDraft
from relayops_fleet.fabric.publisher import publish_campaign_run
from relayops_fleet.fabric.worker import PermanentFailure, run_one_client

pytestmark = pytest.mark.skipif(
    not os.environ.get("RELAYOPS_TEST_DB"),
    reason="set RELAYOPS_TEST_DB=1 to run against a real Postgres",
)

TENANT_A = "Fabric Clinic A (synthetic)"
TENANT_B = "Fabric Clinic B (synthetic)"
AS_OF = date(2026, 8, 16)
PHONE = "+14165550{n:03d}"


@pytest.fixture(scope="module")
def engine():
    eng = repo.build_engine(get_settings().database_url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    Session = repo.build_sessionmaker(engine)
    with Session() as s:
        yield s
        s.rollback()


@pytest.fixture(autouse=True)
def _clean(engine):
    def purge():
        with repo.unguarded(), engine.begin() as conn:
            conn.execute(
                text("DELETE FROM clinics WHERE name IN (:a, :b)"), {"a": TENANT_A, "b": TENANT_B}
            )
            conn.execute(text("DELETE FROM opt_outs WHERE client_key LIKE '+1416555%'"))

    purge()
    yield
    purge()


def _seed(session, name: str, *, count: int, lapsed_days: int = 200) -> Clinic:
    clinic = Clinic(name=name)
    session.add(clinic)
    session.flush()
    for i in range(count):
        session.add(
            Client(
                clinic_id=clinic.id,
                client_key=PHONE.format(n=i),
                first_name=f"Client{i}",
                last_visit=AS_OF - timedelta(days=lapsed_days),
                visit_count=3,
                lifetime_spend_cents=50_000 + i * 1_000,
            )
        )
    session.flush()
    return clinic


# --- Publisher ------------------------------------------------------------


def test_publisher_fans_out_across_every_active_clinic(session) -> None:
    a = _seed(session, TENANT_A, count=3)
    b = _seed(session, TENANT_B, count=2)
    report = publish_campaign_run(
        session, as_of=AS_OF, dry_run=True, clinic_ids=[a.id, b.id]
    )
    assert report.published == 5
    assert report.per_clinic[TENANT_A] == 3
    assert report.per_clinic[TENANT_B] == 2


def test_publisher_skips_clients_who_are_not_lapsed_enough(session) -> None:
    """Below 90 days there is no bucket and so no approved template.

    Enqueueing them would only produce messages the worker must discard.
    """
    a = _seed(session, TENANT_A, count=4, lapsed_days=10)
    report = publish_campaign_run(session, as_of=AS_OF, dry_run=True, clinic_ids=[a.id])
    assert report.published == 0


def test_publisher_reports_what_the_cap_excluded(session) -> None:
    """A cap that silently truncates looks identical to a small clinic."""
    a = _seed(session, TENANT_A, count=6)
    report = publish_campaign_run(
        session, as_of=AS_OF, dry_run=True, max_clients=2, clinic_ids=[a.id]
    )
    assert report.published == 2
    assert report.skipped_by_cap == 4
    assert "NOT enqueued" in report.summary()


def test_inactive_clinic_is_not_enqueued(session) -> None:
    clinic = _seed(session, TENANT_A, count=2)
    clinic.active = False
    session.flush()
    report = publish_campaign_run(session, as_of=AS_OF, dry_run=True, clinic_ids=[clinic.id])
    assert report.published == 0


# --- Worker: gates and replay safety --------------------------------------


def test_gated_client_is_recorded_and_costs_nothing(session) -> None:
    clinic = _seed(session, TENANT_A, count=1)
    key = PHONE.format(n=0)
    consent_repo.record_opt_out(session, phone=key, source="sms")
    session.flush()

    outcome = asyncio.run(
        run_one_client(
            session,
            run_id="r1",
            clinic_id=clinic.id,
            client_key=key,
            as_of=AS_OF,
            dry_run=False,
        )
    )
    assert outcome == "gated:opted_out"

    row = session.execute(
        text(
            "SELECT decided_by, gate_reason, tokens FROM agent_decisions "
            "WHERE clinic_id = :c AND client_key = :k"
        ),
        {"c": clinic.id, "k": key},
    ).one()
    assert row.decided_by == "rule"
    assert row.gate_reason == "opted_out"
    assert row.tokens == 0


def test_replaying_a_gated_message_leaves_one_decision(session) -> None:
    """At-least-once delivery must not produce two verdicts for one client."""
    clinic = _seed(session, TENANT_A, count=1)
    key = PHONE.format(n=0)
    consent_repo.record_opt_out(session, phone=key, source="sms")
    session.flush()

    for _ in range(2):
        asyncio.run(
            run_one_client(
                session, run_id="r1", clinic_id=clinic.id, client_key=key,
                as_of=AS_OF, dry_run=False,
            )
        )
    count = session.execute(
        text("SELECT count(*) FROM client_decisions WHERE clinic_id = :c AND run_id = 'r1'"),
        {"c": clinic.id},
    ).scalar()
    assert count == 1


def test_unknown_client_is_a_permanent_failure(session) -> None:
    clinic = _seed(session, TENANT_A, count=1)
    with pytest.raises(PermanentFailure):
        asyncio.run(
            run_one_client(
                session, run_id="r1", clinic_id=clinic.id,
                client_key="+14165559999", as_of=AS_OF, dry_run=False,
            )
        )


def test_a_client_of_another_clinic_is_not_reachable(session) -> None:
    """Tenant isolation through the worker's own load path."""
    _seed(session, TENANT_A, count=1)
    clinic_b = _seed(session, TENANT_B, count=0)
    with pytest.raises(PermanentFailure):
        asyncio.run(
            run_one_client(
                session, run_id="r1", clinic_id=clinic_b.id,
                client_key=PHONE.format(n=0), as_of=AS_OF, dry_run=False,
            )
        )


# --- Draft upsert ---------------------------------------------------------


def test_redelivery_updates_one_draft_rather_than_adding_a_second(session) -> None:
    clinic = _seed(session, TENANT_A, count=1)
    key = PHONE.format(n=0)
    for body in ("first copy", "second copy"):
        campaign_repo.upsert_draft(
            session, clinic_id=clinic.id, client_key=key, channel="sms", body=body
        )
    session.flush()

    rows = list(
        session.execute(
            text(
                "SELECT body, status FROM outreach_drafts "
                "WHERE clinic_id = :c AND client_key = :k AND channel = 'sms'"
            ),
            {"c": clinic.id, "k": key},
        )
    )
    assert len(rows) == 1
    assert rows[0].body == "second copy"


@pytest.mark.parametrize("frozen_status", ["approved", "rejected", "sent"])
def test_a_rerun_never_touches_a_draft_a_human_has_acted_on(session, frozen_status: str) -> None:
    """The guarantee that makes a nightly re-run safe.

    Without it, a redelivered message could silently revise copy the clinic
    already approved, or revive something they rejected.
    """
    clinic = _seed(session, TENANT_A, count=1)
    key = PHONE.format(n=0)
    session.add(
        OutreachDraft(
            clinic_id=clinic.id,
            client_key=key,
            channel="sms",
            body="human-approved copy",
            status=frozen_status,
        )
    )
    session.flush()

    campaign_repo.upsert_draft(
        session, clinic_id=clinic.id, client_key=key, channel="sms", body="agent rewrite"
    )
    session.flush()

    row = session.execute(
        text(
            "SELECT body, status FROM outreach_drafts "
            "WHERE clinic_id = :c AND client_key = :k AND channel = 'sms'"
        ),
        {"c": clinic.id, "k": key},
    ).one()
    assert row.body == "human-approved copy"
    assert row.status == frozen_status


def test_two_clinics_drafts_do_not_collide(session) -> None:
    """Same phone, two clinics, two independent drafts."""
    a = _seed(session, TENANT_A, count=1)
    b = _seed(session, TENANT_B, count=1)
    key = PHONE.format(n=0)
    campaign_repo.upsert_draft(
        session, clinic_id=a.id, client_key=key, channel="sms", body="clinic A copy"
    )
    campaign_repo.upsert_draft(
        session, clinic_id=b.id, client_key=key, channel="sms", body="clinic B copy"
    )
    session.flush()

    count = session.execute(
        text(
            "SELECT count(*) FROM outreach_drafts "
            "WHERE client_key = :k AND clinic_id IN (:a, :b)"
        ),
        {"k": key, "a": a.id, "b": b.id},
    ).scalar()
    assert count == 2
