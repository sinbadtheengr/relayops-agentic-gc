"""Integration tests against a real Postgres. Skipped without RELAYOPS_TEST_DB.

The unit tests in test_tenant_isolation.py prove the schema's *shape*. These
prove the database actually enforces it — a CHECK constraint that exists in
metadata but was never applied is worth nothing, and the difference only shows
up against a real server.

    RELAYOPS_TEST_DB=1 python -m pytest tests/test_schema_integration.py

Reads DATABASE_URL (written by scripts/setup_cloudsql.py into .env).
"""
from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from relayops_fleet.config import get_settings
from relayops_fleet.db import repo
from relayops_fleet.db.models import Client, Clinic, OptOut

pytestmark = pytest.mark.skipif(
    not os.environ.get("RELAYOPS_TEST_DB"),
    reason="set RELAYOPS_TEST_DB=1 to run against a real Postgres",
)

TENANT_A = "Test Clinic A (synthetic)"
TENANT_B = "Test Clinic B (synthetic)"
SHARED_PHONE = "+14165550199"


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


@pytest.fixture(scope="module", autouse=True)
def _clean(engine):
    """Remove only this module's synthetic tenants, before and after."""

    def purge():
        with repo.unguarded(), engine.begin() as conn:
            conn.execute(
                text("DELETE FROM clinics WHERE name IN (:a, :b)"), {"a": TENANT_A, "b": TENANT_B}
            )
            conn.execute(
                text("DELETE FROM opt_outs WHERE client_key = :k"), {"k": SHARED_PHONE}
            )

    purge()
    yield
    purge()


def test_migration_created_every_table(engine) -> None:
    from relayops_fleet.db.models import GLOBAL_TABLES, TENANT_SCOPED_TABLES

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        ).scalars()
        present = set(rows)
    for table in (*TENANT_SCOPED_TABLES, *GLOBAL_TABLES):
        assert table in present, f"{table} missing — migration not applied?"


def test_two_clinics_may_share_a_client(session) -> None:
    """The relayops-prod bug, asserted against the real constraint.

    A globally-unique client_key would raise IntegrityError on the second
    insert. It must not.
    """
    a = Clinic(name=TENANT_A)
    b = Clinic(name=TENANT_B)
    session.add_all([a, b])
    session.flush()

    for clinic in (a, b):
        session.add(
            Client(
                clinic_id=clinic.id,
                client_key=SHARED_PHONE,
                first_name="Sam",
                last_visit=date(2026, 1, 15),
            )
        )
    session.flush()  # must not raise

    count = session.execute(
        text("SELECT count(*) FROM clients WHERE client_key = :k AND clinic_id IN (:a, :b)"),
        {"k": SHARED_PHONE, "a": a.id, "b": b.id},
    ).scalar()
    assert count == 2


def test_duplicate_client_within_one_clinic_is_rejected(session) -> None:
    clinic = Clinic(name=TENANT_A)
    session.add(clinic)
    session.flush()
    for _ in range(2):
        session.add(
            Client(
                clinic_id=clinic.id,
                client_key=SHARED_PHONE,
                first_name="Sam",
                last_visit=date(2026, 1, 15),
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_opt_out_is_globally_unique(session) -> None:
    session.add(OptOut(client_key=SHARED_PHONE, reason="STOP reply", source="sms"))
    session.flush()
    session.add(OptOut(client_key=SHARED_PHONE, reason="duplicate", source="sms"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_check_constraints_are_live(session) -> None:
    """A status the application never emits must still be refused by Postgres."""
    clinic = Clinic(name=TENANT_A)
    session.add(clinic)
    session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        session.execute(
            text(
                "INSERT INTO outreach_drafts (clinic_id, client_key, channel, body, status) "
                "VALUES (:c, :k, 'sms', 'hi', 'sent_somehow')"
            ),
            {"c": clinic.id, "k": SHARED_PHONE},
        )
        session.flush()


def test_tenant_guard_blocks_a_real_unscoped_query(session) -> None:
    """The guard must fire against a live connection, not only in unit tests."""
    with pytest.raises(repo.TenantIsolationError):
        session.execute(text("SELECT count(*) FROM clients"))


def test_unguarded_context_allows_the_same_query(session) -> None:
    with repo.unguarded():
        session.execute(text("SELECT count(*) FROM clients")).scalar()


def test_bypass_does_not_survive_connection_reuse(engine) -> None:
    """Regression: the bypass must not leak through the connection pool.

    The first version stored it on Connection.info, which SQLAlchemy keeps on
    the pooled connection record. One unguarded() call left that connection
    permanently unguarded, so the guard silently stopped guarding for every
    later checkout. A single-connection pool makes the reuse certain.
    """
    Session = repo.build_sessionmaker(engine)

    with Session() as s, repo.unguarded():
        s.execute(text("SELECT count(*) FROM clients")).scalar()

    # Same pooled connection, new session: the guard must be back on.
    with Session() as s, pytest.raises(repo.TenantIsolationError):
        s.execute(text("SELECT count(*) FROM clients"))


def test_get_clinic_raises_instead_of_creating(session) -> None:
    with pytest.raises(repo.ClinicNotFound):
        repo.get_clinic(session, "No Such Clinic (typo)")
