"""Approval surface against a real Postgres. Skipped without RELAYOPS_TEST_DB.

The dashboard is where "this system never sends" stops being a claim and
becomes a mechanism, so the tests that matter are the negative ones: approve
must not send, and the cooldown must start before a draft can look sent.

    RELAYOPS_TEST_DB=1 python -m pytest tests/test_dashboard_integration.py

Acceptance criteria for F-8 (see CLAUDE.md).
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from relayops_fleet.config import get_settings
from relayops_fleet.db import repo
from relayops_fleet.db.models import Client, ClientDecision, Clinic, OutreachDraft

pytestmark = pytest.mark.skipif(
    not os.environ.get("RELAYOPS_TEST_DB"),
    reason="set RELAYOPS_TEST_DB=1 to run against a real Postgres",
)

TENANT = "Dashboard Clinic (synthetic)"
OTHER = "Dashboard Other Clinic (synthetic)"
PHONE = "+14165550150"
PASSWORD = "test-operator-password"
AUTH = ("operator", PASSWORD)


@pytest.fixture(scope="module")
def engine():
    eng = repo.build_engine(get_settings().database_url)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean(engine):
    def purge():
        with repo.unguarded(), engine.begin() as conn:
            conn.execute(
                text("DELETE FROM clinics WHERE name IN (:a, :b)"), {"a": TENANT, "b": OTHER}
            )

    purge()
    yield
    purge()


@pytest.fixture
def app_client(monkeypatch, engine):
    """A TestClient with the operator password configured."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    get_settings.cache_clear()
    from relayops_fleet.dashboard import app as dash

    dash._Session = repo.build_sessionmaker(engine)
    yield TestClient(dash.app)
    get_settings.cache_clear()


@pytest.fixture
def seeded(engine):
    """One clinic with a draft, plus a second clinic to prove isolation."""
    Session = repo.build_sessionmaker(engine)
    with Session() as s, s.begin():
        clinic = Clinic(name=TENANT)
        other = Clinic(name=OTHER)
        s.add_all([clinic, other])
        s.flush()
        s.add(
            Client(
                clinic_id=clinic.id,
                client_key=PHONE,
                first_name="Dana",
                last_visit=date(2026, 1, 3),
                visit_count=7,
                lifetime_spend_cents=412_000,
            )
        )
        draft = OutreachDraft(
            clinic_id=clinic.id,
            client_key=PHONE,
            channel="sms",
            body="Hi Dana, we'd love to see you. Reply STOP to opt out.",
            status="draft",
            needs_review=False,
        )
        s.add(draft)
        s.add(
            ClientDecision(
                clinic_id=clinic.id,
                client_key="+14165550151",
                run_id="r1",
                target=False,
                decided_by="rule",
                gate_reason="opted_out",
                reasoning="gated: opted_out",
            )
        )
        s.flush()
        ids = (clinic.id, other.id, draft.id)
    return ids


# --- Auth -----------------------------------------------------------------


def test_without_a_password_the_surface_refuses_to_serve(monkeypatch, engine) -> None:
    """Failing closed is the only safe default for a page listing client PII."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    get_settings.cache_clear()
    from relayops_fleet.dashboard import app as dash

    dash._Session = repo.build_sessionmaker(engine)
    client = TestClient(dash.app)
    assert client.get("/").status_code == 503
    get_settings.cache_clear()


def test_wrong_password_is_rejected(app_client) -> None:
    assert app_client.get("/", auth=("operator", "wrong")).status_code == 401


def test_no_credentials_are_rejected(app_client) -> None:
    assert app_client.get("/").status_code == 401


def test_health_needs_no_auth(app_client) -> None:
    """Cloud Run needs it, and it reveals nothing.

    Deliberately /health, not /healthz: Cloud Run's frontend intercepts
    /healthz and returns its own 404 before the request reaches the app.
    """
    assert app_client.get("/health").status_code == 200


def test_api_docs_are_not_served(app_client) -> None:
    """This surface lists client PII; it must not publish its route map."""
    assert app_client.get("/openapi.json").status_code == 404
    assert app_client.get("/docs").status_code == 404


# --- Views ----------------------------------------------------------------


def test_index_lists_clinics_with_counts(app_client, seeded) -> None:
    page = app_client.get("/", auth=AUTH)
    assert page.status_code == 200
    assert TENANT in page.text


def test_drafts_view_shows_the_copy_and_says_approve_does_not_send(
    app_client, seeded
) -> None:
    clinic_id, _other, _draft = seeded
    page = app_client.get(f"/clinics/{clinic_id}/drafts", auth=AUTH)
    assert page.status_code == 200
    assert "Reply STOP to opt out" in page.text
    # The button text is a product requirement, not decoration: an operator who
    # believes Approve dispatched a message will eventually be very surprised.
    assert "does not send" in page.text


def test_skipped_view_shows_who_was_not_contacted_and_why(app_client, seeded) -> None:
    clinic_id, _other, _draft = seeded
    page = app_client.get(f"/clinics/{clinic_id}/skipped", auth=AUTH)
    assert page.status_code == 200
    assert "opted_out" in page.text
    assert "zero tokens" in page.text


def test_a_draft_of_another_clinic_is_not_reachable(app_client, seeded) -> None:
    """A stale link must not read another tenant's client."""
    _clinic_id, other_id, draft_id = seeded
    page = app_client.get(f"/clinics/{other_id}/drafts/{draft_id}/decision", auth=AUTH)
    assert page.status_code == 404


# --- Actions --------------------------------------------------------------


def test_approve_marks_but_does_not_send(app_client, seeded, engine) -> None:
    """The central guarantee of the whole product.

    Approve changes a status. It must not write contact_log — that would start
    a cooldown for a message nobody has actually sent.
    """
    clinic_id, _other, draft_id = seeded
    res = app_client.post(
        f"/clinics/{clinic_id}/drafts/{draft_id}/approve", auth=AUTH, follow_redirects=False
    )
    assert res.status_code == 303

    with repo.unguarded(), engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM outreach_drafts WHERE id = :i"), {"i": draft_id}
        ).scalar()
        contacts = conn.execute(
            text("SELECT count(*) FROM contact_log WHERE clinic_id = :c"), {"c": clinic_id}
        ).scalar()
    assert status == "approved"
    assert contacts == 0, "approving must not start a cooldown"


def test_reject_marks_rejected(app_client, seeded, engine) -> None:
    clinic_id, _other, draft_id = seeded
    app_client.post(
        f"/clinics/{clinic_id}/drafts/{draft_id}/reject", auth=AUTH, follow_redirects=False
    )
    with repo.unguarded(), engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM outreach_drafts WHERE id = :i"), {"i": draft_id}
        ).scalar()
    assert status == "rejected"


def test_mark_sent_starts_the_cooldown_and_flips_the_status(
    app_client, seeded, engine
) -> None:
    """Order matters: contact_log first, status second, one transaction.

    A sent draft whose cooldown never started is how someone gets messaged
    twice.
    """
    clinic_id, _other, draft_id = seeded
    res = app_client.post(
        f"/clinics/{clinic_id}/drafts/{draft_id}/sent", auth=AUTH, follow_redirects=False
    )
    assert res.status_code == 303

    with repo.unguarded(), engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM outreach_drafts WHERE id = :i"), {"i": draft_id}
        ).scalar()
        contacts = conn.execute(
            text("SELECT count(*) FROM contact_log WHERE clinic_id = :c AND client_key = :k"),
            {"c": clinic_id, "k": PHONE},
        ).scalar()
    assert status == "sent"
    assert contacts == 1


def test_marking_sent_puts_the_client_into_cooldown(app_client, seeded, engine) -> None:
    """End-to-end proof the loop closes: the next run must gate this client."""
    from relayops_fleet.core.gates import apply_gates
    from relayops_fleet.db import consent_repo

    clinic_id, _other, draft_id = seeded
    app_client.post(
        f"/clinics/{clinic_id}/drafts/{draft_id}/sent", auth=AUTH, follow_redirects=False
    )

    Session = repo.build_sessionmaker(engine)
    with Session() as s:
        cooldown = consent_repo.recently_contacted_phones(s, clinic_id=clinic_id)
    result = apply_gates(
        raw_phone=PHONE,
        last_visit=date(2026, 1, 3) - timedelta(days=1),
        recently_contacted_phones=cooldown,
    )
    assert result.reason == "cooldown"


def test_acting_on_a_missing_draft_is_404(app_client, seeded) -> None:
    clinic_id, _other, _draft = seeded
    res = app_client.post(
        f"/clinics/{clinic_id}/drafts/999999/approve", auth=AUTH, follow_redirects=False
    )
    assert res.status_code == 404
