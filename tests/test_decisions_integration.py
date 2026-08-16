"""Decision log against a real Postgres. Skipped without RELAYOPS_TEST_DB.

The decision log is a product surface, not a debug table: the approval
dashboard resolves every draft to the row that produced it. These tests pin
the two properties that make that possible — the row lands in the caller's
transaction, and rule-gated clients get a row too.

Acceptance criteria for F-10 (see CLAUDE.md).
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from relayops_fleet.config import get_settings
from relayops_fleet.db import repo
from relayops_fleet.db.models import Clinic
from relayops_fleet.obs.decisions import log_agent_decision, log_gate_decision

pytestmark = pytest.mark.skipif(
    not os.environ.get("RELAYOPS_TEST_DB"),
    reason="set RELAYOPS_TEST_DB=1 to run against a real Postgres",
)

TENANT = "Decision Log Clinic (synthetic)"
PHONE = "+14165550177"


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
            conn.execute(text("DELETE FROM clinics WHERE name = :n"), {"n": TENANT})

    purge()
    yield
    purge()


def _clinic(session) -> int:
    clinic = Clinic(name=TENANT)
    session.add(clinic)
    session.flush()
    return clinic.id


def test_model_decision_records_its_cost(session) -> None:
    clinic_id = _clinic(session)
    row = log_agent_decision(
        session,
        agent_name="segment",
        clinic_id=clinic_id,
        client_key=PHONE,
        inputs={"days_lapsed": 231, "is_vip": True},
        output={"target": True, "priority_tier": "A"},
        reasoning="cited the client's numbers",
        model="gemini-3.7-flash",
        tokens=1227,
        latency_ms=5165,
    )
    assert row.id is not None
    assert row.decided_by == "model"
    assert row.tokens == 1227


def test_gate_decision_costs_nothing_and_names_its_reason(session) -> None:
    """The record of a client the system deliberately did NOT contact.

    This is the half of the audit trail a compliance review actually asks for,
    and nothing downstream depends on it — so a missing row is invisible until
    someone asks why a client was never contacted and the system cannot say.
    """
    clinic_id = _clinic(session)
    row = log_gate_decision(
        session,
        clinic_id=clinic_id,
        client_key=PHONE,
        gate_reason="opted_out",
        inputs={"raw_phone": "416-555-0177"},
    )
    assert row.decided_by == "rule"
    assert row.gate_reason == "opted_out"
    assert row.tokens == 0
    assert row.model == ""
    assert row.output is None


def test_decision_row_joins_back_to_its_clinic(session) -> None:
    clinic_id = _clinic(session)
    log_gate_decision(
        session, clinic_id=clinic_id, client_key=PHONE, gate_reason="cooldown", inputs={}
    )
    found = session.execute(
        text(
            "SELECT count(*) FROM agent_decisions "
            "WHERE clinic_id = :c AND gate_reason = 'cooldown'"
        ),
        {"c": clinic_id},
    ).scalar()
    assert found == 1


def test_decision_shares_the_callers_transaction(session) -> None:
    """A rollback must take the decision row with it.

    If the log opened its own connection, a rolled-back draft could leave an
    orphan decision row claiming a campaign action that never happened.
    """
    clinic_id = _clinic(session)
    log_gate_decision(
        session, clinic_id=clinic_id, client_key=PHONE, gate_reason="suppressed", inputs={}
    )
    session.rollback()

    with repo.unguarded():
        remaining = session.execute(
            text("SELECT count(*) FROM agent_decisions WHERE client_key = :k"), {"k": PHONE}
        ).scalar()
    assert remaining == 0


def test_unserializable_input_does_not_break_the_log(session) -> None:
    """Inputs are stringified rather than dropped: a decision that cannot be
    logged must not become a decision that is silently unlogged."""
    from datetime import date

    clinic_id = _clinic(session)
    row = log_agent_decision(
        session,
        agent_name="segment",
        clinic_id=clinic_id,
        client_key=PHONE,
        inputs={"as_of": date(2026, 8, 16)},
        output=None,
    )
    assert row.input["as_of"] == "2026-08-16"


def test_agent_decisions_is_tenant_guarded(session) -> None:
    with pytest.raises(repo.TenantIsolationError):
        session.execute(text("SELECT count(*) FROM agent_decisions"))


def test_every_model_decision_has_a_clinic(session) -> None:
    """clinic_id is NOT NULL: an unattributed decision cannot be audited."""
    with pytest.raises((IntegrityError, DBAPIError)):
        log_agent_decision(
            session,
            agent_name="segment",
            clinic_id=None,  # type: ignore[arg-type]
            client_key=PHONE,
            inputs={},
        )
