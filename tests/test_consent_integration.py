"""Consent registers against a real Postgres. Skipped without RELAYOPS_TEST_DB.

The pure-function tests in test_gates.py prove the *decision*. These prove the
loaders feed it the right values — in particular that opt-outs are read
globally and cooldown is read per clinic, which is the pair that has been
gotten backwards before.

    RELAYOPS_TEST_DB=1 python -m pytest tests/test_consent_integration.py
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from relayops_fleet.config import get_settings
from relayops_fleet.core.gates import apply_gates
from relayops_fleet.db import consent_repo, repo
from relayops_fleet.db.models import Clinic, ContactLog

pytestmark = pytest.mark.skipif(
    not os.environ.get("RELAYOPS_TEST_DB"),
    reason="set RELAYOPS_TEST_DB=1 to run against a real Postgres",
)

TENANT_A = "Consent Clinic A (synthetic)"
TENANT_B = "Consent Clinic B (synthetic)"
PHONE = "+14165550188"
PHONE_RAW = "416-555-0188"
EMAIL = "dana@example.com"


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
            conn.execute(
                text("DELETE FROM opt_outs WHERE client_key = :p OR email = :e"),
                {"p": PHONE, "e": EMAIL},
            )

    purge()
    yield
    purge()


def _clinics(session) -> tuple[int, int]:
    a, b = Clinic(name=TENANT_A), Clinic(name=TENANT_B)
    session.add_all([a, b])
    session.flush()
    return a.id, b.id


def test_email_only_opt_out_is_recordable(session) -> None:
    """The gap this migration closed: an email unsubscribe with no phone.

    Before 0002 this raised NOT NULL and the unsubscribe was silently lost.
    """
    row = consent_repo.record_opt_out(session, email="Dana@Example.COM", source="unsubscribe")
    session.flush()
    assert row.client_key is None
    assert row.email == EMAIL

    _phones, emails = consent_repo.load_opt_outs(session)
    assert EMAIL in emails


def test_opt_out_is_idempotent(session) -> None:
    """A second STOP from the same number is not an error."""
    first = consent_repo.record_opt_out(session, phone=PHONE_RAW, source="sms")
    session.flush()
    second = consent_repo.record_opt_out(session, phone=PHONE_RAW, source="sms")
    session.flush()
    assert first.id == second.id


def test_opt_out_without_any_identifier_raises(session) -> None:
    """Recording a suppression that matches nothing is worse than failing."""
    with pytest.raises(ValueError):
        consent_repo.record_opt_out(session, phone="not a phone", email="   ")


def test_opt_outs_load_globally_across_clinics(session) -> None:
    """One register, every tenant. The gate must fire for both clinics."""
    _a, _b = _clinics(session)
    consent_repo.record_opt_out(session, phone=PHONE_RAW, source="sms")
    session.flush()

    phones, emails = consent_repo.load_opt_outs(session)
    for _clinic_id in (_a, _b):
        result = apply_gates(
            raw_phone=PHONE_RAW,
            last_visit=datetime.now(UTC).date(),
            opted_out_phones=phones,
            opted_out_emails=emails,
        )
        assert result.reason == "opted_out"


def test_cooldown_does_not_cross_clinics(session) -> None:
    """Clinic A contacting a shared client must not gate clinic B.

    This is the relayops-prod bug, asserted through the real loader: B's query
    must return an empty set even though the same phone was just contacted.
    """
    a_id, b_id = _clinics(session)
    consent_repo.log_contact(session, clinic_id=a_id, client_key=PHONE, channel="sms")
    session.flush()

    a_keys = consent_repo.recently_contacted_phones(session, clinic_id=a_id)
    b_keys = consent_repo.recently_contacted_phones(session, clinic_id=b_id)

    assert PHONE in a_keys
    assert PHONE not in b_keys

    assert (
        apply_gates(
            raw_phone=PHONE_RAW,
            last_visit=datetime.now(UTC).date(),
            recently_contacted_phones=a_keys,
        ).reason
        == "cooldown"
    )
    assert apply_gates(
        raw_phone=PHONE_RAW,
        last_visit=datetime.now(UTC).date(),
        recently_contacted_phones=b_keys,
    ).passed


def test_contact_older_than_the_window_stops_gating(session) -> None:
    """The cooldown expires; suppression from it must expire with it."""
    a_id, _b = _clinics(session)
    stale = datetime.now(UTC) - timedelta(days=90)
    session.add(
        ContactLog(clinic_id=a_id, client_key=PHONE, channel="sms", contacted_at=stale)
    )
    session.flush()

    keys = consent_repo.recently_contacted_phones(session, clinic_id=a_id, cooldown_days=14)
    assert PHONE not in keys


def test_cooldown_query_is_tenant_guarded(session) -> None:
    """The loader must not be able to read every clinic's contact log."""
    with pytest.raises(repo.TenantIsolationError):
        session.execute(text("SELECT client_key FROM contact_log"))
