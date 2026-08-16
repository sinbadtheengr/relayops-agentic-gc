"""Feature computation tests. Pure functions — no database, no model.

These numbers reach the model as authoritative facts and end up quoted in a
clinic-facing invoice, so every branch is pinned.

Acceptance criteria for F-7 (see CLAUDE.md).
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from relayops_fleet.core.features import (
    MIN_SPENDS_FOR_VIP_CUTOFF,
    ClientFeatures,
    compute_features,
    compute_vip_cutoff_cents,
    lapse_bucket,
)

AS_OF = date(2026, 8, 16)


# --- Lapse buckets --------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, None),
        (89, None),
        (90, "lapsed_90_180"),
        (179, "lapsed_90_180"),
        (180, "lapsed_180_365"),
        (364, "lapsed_180_365"),
        (365, "lapsed_365_plus"),
        (5000, "lapsed_365_plus"),
    ],
)
def test_lapse_bucket_boundaries(days: int, expected: str | None) -> None:
    assert lapse_bucket(days) == expected


def test_recent_client_has_no_bucket() -> None:
    """Below 90 days is not a win-back target.

    Returning a bucket would invite the model to campaign at someone who is
    simply a current client.
    """
    assert lapse_bucket(30) is None


# --- VIP cutoff -----------------------------------------------------------


def test_vip_cutoff_needs_enough_data() -> None:
    """In a four-client book, someone is the 80th percentile by construction.

    A cutoff of 0 disables the VIP tier rather than inventing one.
    """
    assert compute_vip_cutoff_cents([100, 200, 300, 400]) == 0
    assert len([100, 200, 300, 400]) < MIN_SPENDS_FOR_VIP_CUTOFF


def test_vip_cutoff_is_a_real_client_spend() -> None:
    """Nearest-rank, not interpolation: the threshold must be defensible when
    a clinic owner asks why someone is a VIP."""
    spends = [10_000, 20_000, 30_000, 40_000, 50_000]
    cutoff = compute_vip_cutoff_cents(spends)
    assert cutoff in spends


def test_vip_cutoff_ignores_unknown_spends() -> None:
    """None is missing data, not zero.

    Counting unreadable spend columns as 0 would drag the cutoff down and
    manufacture VIPs out of ordinary clients.
    """
    with_nones = [10_000, None, 20_000, None, 30_000, 40_000, 50_000]
    assert compute_vip_cutoff_cents(with_nones) == compute_vip_cutoff_cents(
        [10_000, 20_000, 30_000, 40_000, 50_000]
    )


def test_vip_cutoff_selects_roughly_the_top_fifth() -> None:
    spends = list(range(1_000, 101_000, 1_000))  # 100 clients
    cutoff = compute_vip_cutoff_cents(spends)
    above = [s for s in spends if s >= cutoff]
    assert 15 <= len(above) <= 25


# --- Feature assembly -----------------------------------------------------


def _features(**overrides) -> ClientFeatures:
    kwargs = {
        "first_name": "Dana",
        "last_visit": date(2026, 1, 3),
        "as_of": AS_OF,
        "visit_count": 7,
        "lifetime_spend_cents": 412_000,
        "vip_cutoff_cents": 280_000,
        "last_service": "injectables",
    }
    kwargs.update(overrides)
    return compute_features(**kwargs)


def test_days_lapsed_is_measured_from_as_of_not_today() -> None:
    """A run must be reproducible: re-running yesterday's batch produces
    yesterday's numbers, or its decision rows cannot be defended."""
    assert _features().days_lapsed == (AS_OF - date(2026, 1, 3)).days


def test_vip_when_spend_meets_the_cutoff() -> None:
    assert _features(lifetime_spend_cents=280_000).is_vip
    assert not _features(lifetime_spend_cents=279_999).is_vip


def test_unknown_spend_is_never_vip() -> None:
    assert not _features(lifetime_spend_cents=None).is_vip


def test_zero_cutoff_disables_vip_entirely() -> None:
    """A clinic too small for a meaningful percentile has no VIP tier."""
    assert not _features(vip_cutoff_cents=0, lifetime_spend_cents=999_999).is_vip


def test_prompt_dict_carries_the_cutoff_alongside_the_verdict() -> None:
    """The model is shown WHY someone is a VIP, not just that they are, so its
    reasoning can cite the threshold instead of asserting the label."""
    facts = _features().to_prompt_dict()
    assert facts["is_vip"] is True
    assert facts["vip_cutoff_cents"] == 280_000
    assert facts["lifetime_spend_cents"] == 412_000


def test_features_are_frozen() -> None:
    """Nothing downstream — least of all a callback — may edit the facts."""
    with pytest.raises(FrozenInstanceError):
        _features().days_lapsed = 1  # type: ignore[misc]
