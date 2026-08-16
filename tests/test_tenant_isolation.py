"""Tenant isolation tests. These must never be skipped in CI.

They run with no database and no network: the schema is inspected as metadata
and the guard is a pure function over SQL text. There is no excuse for these
being slow, flaky, or disabled.

Acceptance criteria for F-2 (see CLAUDE.md).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from relayops_fleet.db import models, repo

SRC = Path(__file__).resolve().parents[1] / "src" / "relayops_fleet"


# --- Schema shape ---------------------------------------------------------


@pytest.mark.parametrize("table_name", models.TENANT_SCOPED_TABLES)
def test_every_tenant_table_has_clinic_id(table_name: str) -> None:
    """Adding a table to TENANT_SCOPED_TABLES without scoping it fails here."""
    table = models.Base.metadata.tables[table_name]
    assert "clinic_id" in table.columns, f"{table_name} carries PII but is not tenant-scoped"
    assert not table.columns["clinic_id"].nullable, f"{table_name}.clinic_id must be NOT NULL"


def test_opt_outs_is_global() -> None:
    """The absence of clinic_id on opt_outs is the feature, not an oversight.

    Scoping opt-outs per clinic would permit contacting someone who opted out
    elsewhere. Under-suppressing is the compliance risk; over-suppressing only
    costs a lead.
    """
    assert "clinic_id" not in models.OptOut.__table__.columns
    assert models.OptOut.__tablename__ in models.GLOBAL_TABLES


def test_client_key_is_unique_per_clinic_not_globally() -> None:
    """Two clinics may share a customer.

    This was a real bug in relayops-prod: a global unique key on the phone
    number meant one clinic's outreach put the other clinic's same-phone
    customer into cooldown — breaking that campaign and leaking that the two
    clinics share a client.
    """
    uniques = [
        tuple(sorted(c.name for c in constraint.columns))
        for constraint in models.Client.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("client_key", "clinic_id") in uniques
    assert ("client_key",) not in uniques


def test_draft_is_unique_per_client_and_channel() -> None:
    """A redelivered Pub/Sub message must not produce a second draft."""
    uniques = [
        tuple(sorted(c.name for c in constraint.columns))
        for constraint in models.OutreachDraft.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("channel", "client_key", "clinic_id") in uniques


def test_spend_and_visits_are_nullable_but_last_visit_is_not() -> None:
    """None, never 0: a blanked spend must not make a VIP look ordinary.

    last_visit is NOT NULL because a blank date makes everyone look maximally
    lapsed — such rows are skipped at import with a reason instead.
    """
    cols = models.Client.__table__.columns
    assert cols["lifetime_spend_cents"].nullable
    assert cols["visit_count"].nullable
    assert not cols["last_visit"].nullable


# --- The runtime guard ----------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM clients WHERE clinic_id = 1",
        "UPDATE outreach_drafts SET status='approved' WHERE id=5 AND clinic_id=2",
        "DELETE FROM contact_log WHERE clinic_id = 3 AND client_key = '+14165550000'",
        "SELECT * FROM opt_outs WHERE client_key = '+14165550000'",  # global, no scope needed
        "SELECT * FROM clinics",  # the registry itself
        "INSERT INTO clients (clinic_id, client_key) VALUES (1, '+1')",  # NOT NULL is the guard
    ],
)
def test_guard_allows_scoped_and_global_statements(sql: str) -> None:
    assert repo._statement_is_safe(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM clients",
        "SELECT client_key FROM clients WHERE last_visit < '2026-01-01'",
        "UPDATE outreach_drafts SET status='approved' WHERE id=5",
        "DELETE FROM contact_log WHERE client_key = '+14165550000'",
        "SELECT * FROM agent_decisions ORDER BY ts DESC",
        "SELECT * FROM outreach_outcomes WHERE outcome='showed'",
    ],
)
def test_guard_rejects_unscoped_tenant_statements(sql: str) -> None:
    """Each of these would read or mutate across every tenant at once."""
    assert not repo._statement_is_safe(sql)


# --- The never-join rule --------------------------------------------------


def _executable_source(path: Path) -> str:
    """Source with docstrings and comments removed.

    Both rules below are about what the code *does*, not what it says. The
    modules that enforce these rules necessarily describe them in prose —
    models.py's docstring explains the never-join rule at length — and a naive
    substring scan flags that documentation as a violation. It did, on the
    first run of this test.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            first.value.value = ""
    # ast.unparse drops comments as a side effect, which is what we want.
    return ast.unparse(tree)


def test_no_module_references_a_prospects_table() -> None:
    """Track 1 and Track 2 never join.

    Enforced as a test rather than as a convention someone remembers, because
    the failure mode is a consumer-PII leak into a sales dataset.
    """
    offenders = []
    for path in SRC.rglob("*.py"):
        code = _executable_source(path).lower()
        for needle in ("prospects", "prospect_outreach", "prospect_id"):
            if needle in code:
                offenders.append(f"{path.relative_to(SRC)} references {needle!r}")
    assert not offenders, "Track-1 reference found in a Track-2 repo: " + "; ".join(offenders)


def test_core_package_contains_no_llm_calls() -> None:
    """core/ is the compliance and money boundary and must stay verifiable.

    Constraint 1 in CLAUDE.md: no LLM import, client, or call under core/.
    """
    banned = ("google.genai", "google.adk", "genai", "generate_content")
    offenders = []
    for path in (SRC / "core").rglob("*.py"):
        code = _executable_source(path)
        for needle in banned:
            if needle in code:
                offenders.append(f"{path.relative_to(SRC)} references {needle!r}")
    assert not offenders, "LLM reference inside core/: " + "; ".join(offenders)
