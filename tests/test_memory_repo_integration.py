"""Aggregating what converted, against a real Postgres. Needs RELAYOPS_TEST_DB.

The property that matters: memory counts a conversion exactly when the invoice
bills for one. If these could disagree, memory would teach the agent about
outcomes the clinic was never charged for — and the agent's copy would drift
toward whatever the second, unbilled definition happened to reward.

See CLAUDE.md F-9.3.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text

from relayops_fleet.config import get_settings
from relayops_fleet.db import memory_repo, repo
from relayops_fleet.db.models import AgentDecision, Clinic, ContactLog, OutreachOutcome

pytestmark = pytest.mark.skipif(
    not os.environ.get("RELAYOPS_TEST_DB"),
    reason="set RELAYOPS_TEST_DB=1 to run against a real Postgres",
)

TENANT = "Campaign Memory Clinic (synthetic)"
OTHER_TENANT = "Campaign Memory Neighbour (synthetic)"

AS_OF = date(2026, 8, 20)
VIP_CUTOFF = 280_000


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
                text("DELETE FROM clinics WHERE name IN (:a, :b)"),
                {"a": TENANT, "b": OTHER_TENANT},
            )

    purge()
    yield
    purge()


def _clinic(session, name: str = TENANT) -> int:
    clinic = Clinic(name=name)
    session.add(clinic)
    session.flush()
    return clinic.id


def _drafted(
    session,
    *,
    clinic_id: int,
    client_key: str,
    days_lapsed: int,
    spend_cents: int,
) -> None:
    """Record the outreach decision that says which segment a client was in."""
    session.add(
        AgentDecision(
            agent_name="outreach",
            clinic_id=clinic_id,
            client_key=client_key,
            input={
                "client_row": {
                    "last_visit": (AS_OF - timedelta(days=days_lapsed)).isoformat(),
                    "visit_count": 4,
                    "lifetime_spend_cents": spend_cents,
                },
                "vip_cutoff_cents": VIP_CUTOFF,
                "as_of": AS_OF.isoformat(),
            },
            output={},
            model="gemini-3.5-flash",
            tokens=100,
        )
    )
    session.flush()


def _contacted(session, *, clinic_id: int, client_key: str, days_ago: int, channel="sms") -> None:
    session.add(
        ContactLog(
            clinic_id=clinic_id,
            client_key=client_key,
            channel=channel,
            contacted_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    session.flush()


def _showed(session, *, clinic_id: int, client_key: str, days_ago: int) -> None:
    session.add(
        OutreachOutcome(
            clinic_id=clinic_id,
            client_key=client_key,
            outcome="showed",
            occurred_on=datetime.now(UTC).date() - timedelta(days=days_ago),
        )
    )
    session.flush()


def test_contacted_and_converted_are_counted_per_segment(session) -> None:
    clinic_id = _clinic(session)
    for i, key in enumerate(("+14165550301", "+14165550302", "+14165550303")):
        _drafted(session, clinic_id=clinic_id, client_key=key, days_lapsed=200, spend_cents=90_000)
        _contacted(session, clinic_id=clinic_id, client_key=key, days_ago=20)
        if i < 2:
            _showed(session, clinic_id=clinic_id, client_key=key, days_ago=5)

    results = memory_repo.clinic_segment_results(session, clinic_id=clinic_id)
    assert len(results) == 1
    assert results[0].lapse_bucket == "lapsed_180_365"
    assert results[0].is_vip is False
    assert results[0].channel == "sms"
    assert results[0].contacted == 3
    assert results[0].converted == 2


def test_vip_and_standard_are_separate_segments(session) -> None:
    """They are written from different approved sections, so they must not merge."""
    clinic_id = _clinic(session)
    _drafted(
        session, clinic_id=clinic_id, client_key="+14165550311", days_lapsed=200, spend_cents=90_000
    )
    _drafted(
        session, clinic_id=clinic_id, client_key="+14165550312", days_lapsed=200, spend_cents=400_000
    )
    for key in ("+14165550311", "+14165550312"):
        _contacted(session, clinic_id=clinic_id, client_key=key, days_ago=20)

    results = memory_repo.clinic_segment_results(session, clinic_id=clinic_id)
    assert {r.is_vip for r in results} == {True, False}


def test_a_show_outside_the_attribution_window_does_not_convert(session) -> None:
    """Memory and the invoice use one definition of conversion, not two."""
    clinic_id = _clinic(session)
    key = "+14165550321"
    _drafted(session, clinic_id=clinic_id, client_key=key, days_lapsed=200, spend_cents=90_000)
    _contacted(session, clinic_id=clinic_id, client_key=key, days_ago=120)
    _showed(session, clinic_id=clinic_id, client_key=key, days_ago=1)

    results = memory_repo.clinic_segment_results(session, clinic_id=clinic_id)
    assert results[0].contacted == 1
    assert results[0].converted == 0


def test_a_client_contacted_twice_converts_at_most_once(session) -> None:
    """Otherwise a rate could exceed 100% and the memory would be nonsense."""
    clinic_id = _clinic(session)
    key = "+14165550331"
    _drafted(session, clinic_id=clinic_id, client_key=key, days_lapsed=200, spend_cents=90_000)
    _contacted(session, clinic_id=clinic_id, client_key=key, days_ago=25)
    _contacted(session, clinic_id=clinic_id, client_key=key, days_ago=20)
    _showed(session, clinic_id=clinic_id, client_key=key, days_ago=5)

    results = memory_repo.clinic_segment_results(session, clinic_id=clinic_id)
    assert results[0].contacted == 2
    assert results[0].converted == 1


def test_a_contact_with_no_outreach_decision_is_dropped(session) -> None:
    """A client contacted outside the pipeline teaches nothing about a template."""
    clinic_id = _clinic(session)
    _contacted(session, clinic_id=clinic_id, client_key="+14165550341", days_ago=20)
    assert memory_repo.clinic_segment_results(session, clinic_id=clinic_id) == []


def test_a_draft_nobody_sent_is_not_counted(session) -> None:
    """Counting it would dilute every rate with copy no client ever read."""
    clinic_id = _clinic(session)
    _drafted(
        session, clinic_id=clinic_id, client_key="+14165550351", days_lapsed=200, spend_cents=90_000
    )
    assert memory_repo.clinic_segment_results(session, clinic_id=clinic_id) == []


def test_one_clinics_results_never_include_anothers(session) -> None:
    """The tenant boundary, at the source of the facts rather than at the store."""
    mine = _clinic(session)
    theirs = _clinic(session, OTHER_TENANT)
    key = "+14165550361"
    for clinic_id in (mine, theirs):
        _drafted(
            session, clinic_id=clinic_id, client_key=key, days_lapsed=200, spend_cents=90_000
        )
        _contacted(session, clinic_id=clinic_id, client_key=key, days_ago=20)
    _showed(session, clinic_id=theirs, client_key=key, days_ago=5)

    mine_results = memory_repo.clinic_segment_results(session, clinic_id=mine)
    assert mine_results[0].contacted == 1
    assert mine_results[0].converted == 0, "the neighbour's show must not count here"
