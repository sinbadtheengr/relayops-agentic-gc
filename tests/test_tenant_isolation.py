"""Tenant isolation tests. These are the ones that must never be skipped in CI.

Acceptance criteria for F-2 (see CLAUDE.md):
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="F-2 not implemented")


def test_every_track2_query_filters_by_clinic_id() -> None:
    """Structural test: no repo function reads a tenant table without a
    clinic_id predicate."""


def test_message_without_clinic_id_is_dead_lettered_not_inferred() -> None:
    """Guessing the tenant writes one clinic's client into another's campaign."""


def test_no_module_in_this_repo_references_a_prospects_table() -> None:
    """The Track-1 / Track-2 never-join rule, enforced as a test rather than
    as a convention someone remembers."""


def test_vip_percentile_is_computed_within_clinic() -> None:
    """A cross-tenant percentile leaks one clinic's price band into another's
    targeting."""
